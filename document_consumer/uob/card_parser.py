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
    found_end_of_transactions = False
    card_snapshot_content_type = ContentType.objects.get_for_model(CardSnapshot)
    card_transaction_header_pattern = '^(\\d{4}-\\d{4}-\\d{4}-\\d{4}) .+$'

    for i, page in enumerate(pages):
        elements = page.elements
        transaction_tables = []
        elements_index = summary_end_index + 1 if i == 0 else 0
        description_x_coor = None

        # Find transaction tables
        while elements_index < len(elements) and not found_end_of_transactions:
            element = elements[elements_index]

            if (type(element) is ExtractedPdfElement and
                    element.el.text == '-------------------------------------------------- End of Transaction Details -----------------------------------------------------'):
                found_end_of_transactions = True
            elif (type(element) is ExtractedPdfElement and
                  (card_number_match := re.search(card_transaction_header_pattern, element.el.text))):
                card_number = card_number_match.group(1)
                if card_number not in card_with_transactions:
                    card_with_transactions[card_number] = []
                description_x_coor = elements[elements_index + 3].x0
                transaction_tables.append({
                    'card_number': card_number,
                    'description_table': cast(ExtractedTable, elements[elements_index + 7]),
                    'dates_table': cast(ExtractedTable, elements[elements_index + 8]),
                    'excluded_groups': []
                })
                elements_index += 8  # increment by 1 less because it will be incremented again later
            elif element.x0 == description_x_coor:
                transaction_tables[-1]['excluded_groups'].append(element.el)

            elements_index += 1

        # Create transactions for one table
        for table_properties in transaction_tables:
            card_number = table_properties['card_number']
            transactions_on_card = card_with_transactions[card_number]
            # First row is always currency in value field
            description_items = table_properties['description_table'].items[1:]
            # y0 coor point to transaction
            last_transaction = None
            transactions = {}

            # Parse into into individual transactions
            for item in description_items:
                description_group = item.base_element_groups.pop()
                description = description_group.text
                y_coor = description_group.y0

                value = item.values[0].val
                cash_rebate = None
                amount = None
                if value.endswith('CR'):
                    cash_rebate = Decimal(value.replace(' CR', '').replace(',', ''))
                elif value != '':
                    amount = Decimal(value.replace(',', ''))
                else:
                    last_transaction['sub_description_rows'].append(description)
                    continue

                last_transaction = {
                    'y_coor': y_coor,
                    'description': description,
                    'amount': amount,
                    'cash_rebate': cash_rebate,
                    'sub_description_rows': []
                }
                transactions[y_coor] = last_transaction

            # Merge sub-descriptions that are left out
            for group in table_properties['excluded_groups']:
                assert last_transaction['y_coor'] > group.y0
                last_transaction['sub_description_rows'].append(group.text)

            # Merge dates to transactions
            for item in table_properties['dates_table'].items:
                for j, value in enumerate(item.values):
                    field_name = 'post_date' if j == 0 else 'date'
                    transactions[value.el.y0][field_name] = (datetime.datetime
                                                             .strptime(f'{value.el.text} {year}', '%d %b %Y'))

            # Persist each transaction
            for transaction_dict in transactions.values():
                # Unnecessary to model
                transaction_dict.pop('y_coor')
                sub_description = '\n'.join(transaction_dict.pop('sub_description_rows'))
                transaction_dict['sub_description'] = sub_description
                row_number = len(transactions_on_card) + 1  # 1 begin list index
                card_snapshot = card_snapshots[card_number]
                transaction, transaction_created = (CardTransaction.objects
                                                    .get_or_create(snapshot_content_type=card_snapshot_content_type,
                                                                   snapshot_id=card_snapshot.id,
                                                                   row_number=row_number,
                                                                   defaults=transaction_dict))

                transactions_on_card.append(transaction)

    return card_with_transactions
