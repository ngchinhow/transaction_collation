import datetime
import re
from decimal import Decimal
from typing import List, cast

from django.contrib.contenttypes.models import ContentType
from pdf_reader.custom_dataclasses import ExtractedPage, \
    PdfParagraph, \
    BaseElementGroup, \
    ExtractedTable, \
    ExtractedPdfElement

from components.models import FinancialInstitution, \
    Address, \
    InstrumentHolder, \
    Card, \
    Statement, \
    InstrumentStatement, \
    CardSnapshot, CardTransaction


def parse_uob_card_statement(file_name: str, pages: List[ExtractedPage], fi: FinancialInstitution):
    statement_date = parse_uob_card_statement_month(pages[-1])
    card_snapshots, summary_end_index, statement_year = parse_uob_card_metadata(file_name,
                                                                                statement_date,
                                                                                pages[0],
                                                                                fi)
    card_transactions = parse_uob_card_transactions(pages[:-2], card_snapshots, summary_end_index, statement_year)


def parse_uob_card_statement_month(last_page: ExtractedPage):
    for element in last_page.elements:
        match = re.search('Please pay by (\\d{2} \\w{3} \\d{4})', element.get_text())
        if match:
            return datetime.datetime.strptime(match.group(1), '%d %b %Y').date() - datetime.timedelta(days=21)


def parse_uob_card_metadata(file_name: str,
                            statement_date: datetime.date,
                            first_page: ExtractedPage,
                            fi: FinancialInstitution):
    first_page_second_paragraph = cast(PdfParagraph, first_page.paragraphs[1])
    # Instrument holder name
    instrument_holder_name = ' '.join(word.text.capitalize() for word in
                                      cast(BaseElementGroup, first_page_second_paragraph.elements[0].el).elements[1:])

    # Instrument holder address
    holder_address_text = ' '.join([row.get_text() for row in first_page_second_paragraph.elements[1:]])
    holder_address_text = ' '.join([words.capitalize() for words in holder_address_text.split(' ')])
    holder_address, holder_address_created = Address.objects.get_or_create(full_address=holder_address_text)
    holder, holder_created = InstrumentHolder.objects.get_or_create(full_name=instrument_holder_name,
                                                                    address=holder_address)

    first_page_elements = first_page.elements
    # Statement day, statement year, currency and total credit limit
    first_page_eighth_element_items = cast(ExtractedTable, first_page_elements[7]).items
    total_credit_limit = None
    currency = None
    for item in first_page_eighth_element_items:
        if item.el.text == 'Total Credit Limit':
            parts = ''.join([val.val for val in item.values]).split(' ')
            currency = parts[0]
            total_credit_limit = Decimal(parts[1].replace(',', ''))

    # Statement
    statement, statement_created = Statement.objects.get_or_create(holder=holder,
                                                                   provider=fi,
                                                                   date=statement_date,
                                                                   type=Statement.InstrumentType.CARD,
                                                                   defaults={'file_name': file_name})

    # Cards
    i = 10
    found_cards_table = False
    card_name_x_coor = None
    card_number_x_coor = None
    card_holder_x_coor = None
    cards = {}
    summary_end_index = None
    card_content_type = ContentType.objects.get_for_model(Card)
    while i < len(first_page_elements):
        element_i = first_page_elements[i]
        if element_i.get_text() == 'Credit Card(s) Statement' and first_page_elements[i + 1].get_text() == 'Summary':
            found_cards_table = True
            i += 2
            continue

        if found_cards_table:
            if isinstance(element_i, ExtractedTable):
                for item in element_i.items[:-1]:
                    card_name = None
                    card_number = None
                    for group in item.base_element_groups:
                        if group.x0 == card_name_x_coor:
                            card_name = group.text
                        elif group.x0 == card_number_x_coor:
                            card_number = group.text
                        elif group.x0 == card_holder_x_coor:
                            name_on_card = ' '.join([word.capitalize() for word in group.text.split(' ')])
                            assert name_on_card == instrument_holder_name

                    card, card_created = Card.objects.get_or_create(holder=holder,
                                                                    provider=fi,
                                                                    name=card_name,
                                                                    number=card_number,
                                                                    currency=currency)
                    card_statement, card_statement_created = (InstrumentStatement.objects
                                                              .get_or_create(instrument_content_type=card_content_type,
                                                                             instrument_id=card.id,
                                                                             statement=statement))
                    card_snapshot, card_snapshot_created = (CardSnapshot.objects
                                                            .get_or_create(instrument_statement=card_statement,
                                                                           defaults={
                                                                               'total_credit_limit': total_credit_limit
                                                                           }))
                    # Add card to dict
                    cards[card_number] = card_snapshot

                # Note where the summary ends
                summary_end_index = i
                # Stop for the scope of this function
                break
            else:
                match element_i.get_text():
                    case 'Card Name':
                        card_name_x_coor = element_i.x0
                    case 'Card Number':
                        card_number_x_coor = element_i.x0
                    case 'Name on Card':
                        card_holder_x_coor = element_i.x0
        i += 1

    return cards, summary_end_index, statement_date.year


def parse_uob_card_transactions(pages: List[ExtractedPage], card_snapshots: dict, summary_end_index: int, year: int):
    card_with_transactions = {}
    latest_card_snapshot = None
    found_end_of_transactions = False
    card_snapshot_content_type = ContentType.objects.get_for_model(CardSnapshot)
    card_transaction_header_pattern = '^(\\d{4}-\\d{4}-\\d{4}-\\d{4}) ([\\w\\s]+).*$'

    for i, page in enumerate(pages):
        elements = page.elements
        transaction_tables = {}
        latest_transaction_rows = {}
        elements_index = summary_end_index + 1 if i == 0 else 0
        date_currency_y_coor = None  # y coordinate of second row of header row
        description_x_coor = None
        amount_x_start_coor = None
        amount_x_end_coor = None

        # Find transaction tables
        while elements_index < len(elements) and not found_end_of_transactions:
            element = elements[elements_index]

            if (type(element) is ExtractedPdfElement and
                    element.el.text == '-------------------------------------------------- End of Transaction Details -----------------------------------------------------'):
                found_end_of_transactions = True

            elif (type(element) is ExtractedPdfElement and
                  (card_number_match := re.search(card_transaction_header_pattern, element.el.text))):
                # Find table coordinates and prepare to gather elements
                card_number = card_number_match.group(1)
                if card_number not in card_snapshots:
                    # Card is a supplementary card
                    holder_name = ' '.join([word.capitalize() for word in
                                            card_number_match.group(2).rstrip().split(' ')])
                    holder, holder_created = InstrumentHolder.objects.get_or_create(full_name=holder_name)

                    latest_card_statement = latest_card_snapshot.instrument_statement
                    latest_card = latest_card_statement.instrument
                    card, card_created = Card.objects.get_or_create(holder=holder,
                                                                    provider=latest_card.provider,
                                                                    name=latest_card.name,
                                                                    number=card_number,
                                                                    currency=latest_card.currency,
                                                                    parent=latest_card)
                    card_statement, card_statement_created = (InstrumentStatement.objects
                                                              .get_or_create(instrument_content_type=latest_card_statement.instrument_content_type,
                                                                             instrument_id=card.id,
                                                                             statement=latest_card_statement.statement))
                    card_snapshot, card_snapshot_created = (CardSnapshot.objects
                                                            .get_or_create(instrument_statement=card_statement,
                                                                           defaults={
                                                                               'total_credit_limit': latest_card_snapshot.total_credit_limit
                                                                           }))
                    latest_card_snapshot = card_snapshot
                else:
                    latest_card_snapshot = card_snapshots[card_number]
                if latest_card_snapshot not in card_with_transactions:
                    card_with_transactions[latest_card_snapshot] = []
                description_x_coor = elements[elements_index + 3].x0
                amount_x_start_coor = elements[elements_index + 4].x0
                amount_x_end_coor = elements[elements_index + 4].x1
                date_currency_y_coor = elements[elements_index + 5].y0
                transaction_tables[latest_card_snapshot] = {}
                latest_transaction_rows = transaction_tables[latest_card_snapshot]
                elements_index += 6  # increment by 1 less because it will be incremented again later

            elif type(element) is ExtractedPdfElement and element.x0 == description_x_coor:
                if element.y0 not in latest_transaction_rows:
                    latest_transaction_rows[element.y0] = {}
                latest_transaction_rows[element.y0]['description'] = element.el

            elif (type(element) is ExtractedPdfElement and
                  amount_x_start_coor is not None and
                  amount_x_end_coor is not None and
                  date_currency_y_coor is not None and
                  element.x0 > amount_x_start_coor and
                  element.x1 >= amount_x_end_coor and
                  element.y0 != date_currency_y_coor):
                latest_transaction_rows[element.y0]['amount'] = element.el

            elif type(element) is ExtractedTable and element.x0 >= description_x_coor:
                # Description + transaction amount table
                description_items = cast(ExtractedTable, element).items[1:]
                for item in description_items:
                    description_element_group = item.el
                    y_coor = description_element_group.y0
                    if y_coor not in latest_transaction_rows:
                        latest_transaction_rows[y_coor] = {}

                    latest_transaction_rows[y_coor]['description'] = description_element_group
                    latest_transaction_rows[y_coor]['amount'] = item.values[0].el

            elif type(element) is ExtractedTable and element.x0 < description_x_coor:
                # Post + trans dates table
                for item in cast(ExtractedTable, element).items:
                    for j, value in enumerate(item.values):
                        value_el = value.el
                        y_coor = value_el.y0
                        field_name = 'post_date' if j == 0 else 'date'
                        date_value = datetime.datetime.strptime(f'{value_el.text} {year}', '%d %b %Y')
                        if y_coor not in latest_transaction_rows:
                            latest_transaction_rows[y_coor] = {}
                        latest_transaction_rows[y_coor][field_name] = date_value

            elements_index += 1

        # Create transactions for one table
        for snapshot, table_rows in transaction_tables.items():
            transactions_on_card = card_with_transactions[snapshot]
            # Sort via descending y value (top of page to bottom)
            table_rows = dict(sorted(table_rows.items(), key=lambda el: el[0], reverse=True))
            last_transaction = None
            for transaction in table_rows.values():
                if 'amount' in transaction and transaction['amount'] is not None:
                    # Beginning of a new transaction

                    # Save previous transaction
                    if last_transaction is not None:
                        last_transaction['row_number'] = len(transactions_on_card) + 1
                        transactions_on_card.append(last_transaction)

                    # Create new transaction
                    transaction['description'] = transaction['description'].text
                    amount_elements = transaction.pop('amount').elements
                    amount_elements_decimal = Decimal(amount_elements[0].text.replace(',', ''))
                    if len(amount_elements) == 2 and amount_elements[1].text == 'CR':
                        transaction['cash_rebate'] = amount_elements_decimal
                    else:
                        transaction['amount'] = amount_elements_decimal

                    transaction['sub_description'] = ''
                    last_transaction = transaction
                else:
                    # Sub-description row
                    last_transaction['sub_description'] += transaction['description'].text + '\n'

            # Save last transaction
            last_transaction['row_number'] = len(transactions_on_card) + 1
            transactions_on_card.append(last_transaction)

    # Persist each transaction
    snapshot_to_transactions = {}
    for snapshot, transactions_list in card_with_transactions.items():
        snapshot_to_transactions[snapshot] = []
        for transaction_dict in transactions_list:
            transaction, transaction_created = (CardTransaction.objects
                                                .get_or_create(snapshot_content_type=card_snapshot_content_type,
                                                               snapshot_id=snapshot.id,
                                                               row_number=transaction_dict.pop('row_number'),
                                                               defaults=transaction_dict))

            snapshot_to_transactions[snapshot].append(transaction)

    return snapshot_to_transactions
