import datetime
import decimal
import logging
import re
import sys
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
    card_content_type = ContentType.objects.get_for_model(Card)
    card_snapshots, summary_end_index, statement, currency, total_credit_limit = parse_uob_card_metadata(
        file_name,
        statement_date,
        pages[0],
        fi,
        card_content_type)
    card_transactions = parse_uob_card_transactions(pages[:-2],
                                                    card_snapshots,
                                                    summary_end_index,
                                                    statement,
                                                    currency,
                                                    card_content_type,
                                                    total_credit_limit)


def parse_uob_card_statement_month(last_page: ExtractedPage):
    for element in last_page.elements:
        match = re.search('Please pay by (\\d{2} \\w{3} \\d{4})', element.get_text())
        if match:
            return datetime.datetime.strptime(match.group(1), '%d %b %Y').date() - datetime.timedelta(days=21)


def parse_uob_card_metadata(file_name: str,
                            statement_date: datetime.date,
                            first_page: ExtractedPage,
                            fi: FinancialInstitution,
                            card_content_type: ContentType):
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
    cards_by_card_number = {}
    cards_by_y_coor = {}
    summary_end_y_coor = None
    summary_end_index = None

    while i < len(first_page_elements):
        element_i = first_page_elements[i]
        if element_i.get_text() == 'Credit Card(s) Statement' and first_page_elements[i + 1].get_text() == 'Summary':
            found_cards_table = True
            i += 2
            continue

        if found_cards_table:
            if summary_end_y_coor is not None and element_i.y0 < summary_end_y_coor:
                summary_end_index = i
                break
            elif element_i.get_text() == 'Card Name':
                card_name_x_coor = element_i.x0
            elif element_i.get_text() == 'Card Number':
                card_number_x_coor = element_i.x0
            elif element_i.get_text() == 'Name on Card':
                card_holder_x_coor = element_i.x0
            elif isinstance(element_i, ExtractedTable):
                for item in element_i.items[:-1]:
                    y_coor = item.el.y0
                    for group in item.base_element_groups:
                        if group.x0 == card_name_x_coor:
                            prepare_card_dict(cards_by_y_coor, y_coor, 'name', group.text)
                        elif group.x0 == card_number_x_coor:
                            prepare_card_dict(cards_by_y_coor, y_coor, 'number', group.text)
                        elif group.x0 == card_holder_x_coor:
                            name_on_card = ' '.join([word.capitalize() for word in group.text.split(' ')])
                            prepare_card_dict(cards_by_y_coor, y_coor, 'holder', name_on_card)
                            assert name_on_card in instrument_holder_name

                # Note where the summary ends
                summary_end_y_coor = element_i.items[-1].el.y0
            elif card_name_x_coor is not None and element_i.x0 == card_name_x_coor:
                prepare_card_dict(cards_by_y_coor, element_i.y0, 'name', element_i.get_text())
            elif card_number_x_coor is not None and element_i.x0 == card_number_x_coor:
                prepare_card_dict(cards_by_y_coor, element_i.y0, 'number', element_i.get_text())
            elif card_holder_x_coor is not None and element_i.x0 == card_holder_x_coor:
                name_on_card = ' '.join([word.capitalize() for word in element_i.get_text().split(' ')])
                prepare_card_dict(cards_by_y_coor, element_i.y0, 'holder', name_on_card)

        i += 1

    # Group card details that spread across multiple lines
    last_y_coor = sys.maxsize
    last_card_dict = None
    grouped_card_details = []
    for y_coor, card_dict in cards_by_y_coor.items():
        if last_y_coor - y_coor < 12:
            for key, value in last_card_dict.items():
                if key in card_dict:
                    last_card_dict[key] += ' ' + card_dict[key]
        else:
            if last_card_dict is not None:
                grouped_card_details.append(last_card_dict)
            else:
                grouped_card_details.append(card_dict)
            last_card_dict = card_dict
        last_y_coor = y_coor

    # Persist card details
    for card_detail in grouped_card_details:
        card, card_created = Card.objects.get_or_create(holder=holder,
                                                        provider=fi,
                                                        name=card_detail['name'],
                                                        name_on_card=card_detail['holder'],
                                                        number=card_detail['number'],
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
        cards_by_card_number[card_detail['number']] = card_snapshot

    return cards_by_card_number, summary_end_index, statement, currency, total_credit_limit


def parse_uob_card_transactions(pages: List[ExtractedPage],
                                card_snapshots: dict,
                                summary_end_index: int,
                                statement: Statement,
                                currency: str,
                                card_content_type: ContentType,
                                total_credit_limit: Decimal):
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
        post_x_coor = None
        trans_x_coor = None
        date_currency_y_coor = None
        description_x_coor = None
        amount_x_start_coor = None
        amount_x_end_coor = None

        def parse_element_into_field(inner_group: BaseElementGroup):
            if (post_x_coor is not None and
                    abs(inner_group.x0 - post_x_coor) <= 2 < abs(inner_group.y0 - date_currency_y_coor)):
                # Post date
                extract_date(inner_group, latest_transaction_rows, statement.date.year, 'post_date')
            elif (trans_x_coor is not None and
                  abs(inner_group.x0 - trans_x_coor) <= 2 < abs(inner_group.y0 - date_currency_y_coor)):
                # Transaction date
                extract_date(inner_group, latest_transaction_rows, statement.date.year, 'date')
            elif description_x_coor is not None and inner_group.x0 == description_x_coor:
                # Description
                extract_description(inner_group, latest_transaction_rows)
            elif (amount_x_start_coor is not None and
                  amount_x_end_coor is not None and
                  inner_group.x1 >= amount_x_end_coor and
                  abs(inner_group.y0 - date_currency_y_coor) > 2):
                # Amount
                extract_credit_card_amount(inner_group, latest_transaction_rows)

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
                card_name = elements[elements_index - 1].get_text()
                if card_number not in card_snapshots:
                    # Card is a supplementary card
                    name_on_card = ' '.join([word.capitalize() for word in
                                             card_number_match.group(2).rstrip().split(' ')])

                    parent_card = None
                    if latest_card_snapshot is not None:
                        latest_card = latest_card_snapshot.instrument_statement.instrument
                        parent_card = latest_card if latest_card.name == card_name else None

                    card, card_created = Card.objects.get_or_create(holder=statement.holder,
                                                                    provider=statement.provider,
                                                                    name=card_name,
                                                                    number=card_number,
                                                                    name_on_card=name_on_card,
                                                                    currency=currency,
                                                                    defaults={
                                                                        'parent': parent_card
                                                                    })
                    card_statement, card_statement_created = (InstrumentStatement.objects
                                                              .get_or_create(instrument_content_type=card_content_type,
                                                                             instrument_id=card.id,
                                                                             statement=statement))
                    card_snapshot, card_snapshot_created = (CardSnapshot.objects
                                                            .get_or_create(instrument_statement=card_statement,
                                                                           defaults={
                                                                               'total_credit_limit': total_credit_limit
                                                                           }))
                    latest_card_snapshot = card_snapshot
                else:
                    latest_card_snapshot = card_snapshots[card_number]
                if latest_card_snapshot not in card_with_transactions:
                    card_with_transactions[latest_card_snapshot] = []
                transaction_tables[latest_card_snapshot] = {}
                latest_transaction_rows = transaction_tables[latest_card_snapshot]

            elif element.get_text() == 'Post':
                post_x_coor = element.x0
                date_currency_y_coor = element.y0 - 8
            elif element.get_text() == 'Trans':
                trans_x_coor = element.x0
                date_currency_y_coor = element.y0 - 8
            elif element.get_text() == 'Description of Transaction':
                description_x_coor = element.x0
            elif element.get_text() == 'Transaction Amount':
                amount_x_start_coor = element.x0
                amount_x_end_coor = element.x1
            elif type(element) is ExtractedPdfElement:
                parse_element_into_field(cast(BaseElementGroup, element.el))
            elif type(element) is ExtractedTable:
                # Description + transaction amount table
                description_items = cast(ExtractedTable, element).items
                for item in description_items:
                    for group in item.base_element_groups:
                        parse_element_into_field(group)

                    for value in item.values:
                        if value.el is not None:
                            parse_element_into_field(value.el)

            elements_index += 1

        # Create transactions for one table
        for snapshot, table_rows in transaction_tables.items():
            transactions_on_card = card_with_transactions[snapshot]
            # Sort via descending y value (top of page to bottom)
            table_rows = dict(sorted(table_rows.items(), key=lambda el: el[0], reverse=True))
            last_y_coor = None
            last_transaction = None
            for y_coor, transaction_dict in table_rows.items():
                if last_y_coor is not None and last_y_coor - y_coor <= 10:
                    # Merge rows that did not get parsed into the same logical row
                    if 'description' in transaction_dict:
                        if 'sub_description' not in last_transaction:
                            last_transaction['sub_description'] = []
                        last_transaction['sub_description'].append(transaction_dict.pop('description'))
                    last_transaction = last_transaction | transaction_dict
                else:
                    # Save previous transaction
                    if last_transaction is not None:
                        last_transaction['row_number'] = len(transactions_on_card) + 1
                        transactions_on_card.append(last_transaction)
                    # Beginning of a new transaction
                    last_transaction = transaction_dict

                last_y_coor = y_coor

            # Save last transaction
            last_transaction['row_number'] = len(transactions_on_card) + 1
            transactions_on_card.append(last_transaction)

    # Persist each transaction
    snapshot_to_transactions = {}
    for snapshot, transactions_list in card_with_transactions.items():
        snapshot_to_transactions[snapshot] = []
        for transaction_dict in transactions_list:
            if 'sub_description' in transaction_dict:
                transaction_dict['sub_description'] = '\n'.join(transaction_dict['sub_description'])
            transaction, transaction_created = (CardTransaction.objects
                                                .get_or_create(snapshot_content_type=card_snapshot_content_type,
                                                               snapshot_id=snapshot.id,
                                                               row_number=transaction_dict.pop('row_number'),
                                                               defaults=transaction_dict))

            snapshot_to_transactions[snapshot].append(transaction)

    return snapshot_to_transactions


def extract_date(group: BaseElementGroup, rows: dict, year: int, key: str):
    try:
        date = datetime.datetime.strptime(f'{group.text} {year}', '%d %b %Y')
    except ValueError:
        logging.debug(f'String \'{group.text}\' is not a date value.')
        return
    prepare_card_dict(rows, group.y0, key, date)


def extract_description(group: BaseElementGroup, rows: dict):
    prepare_card_dict(rows, group.y0, 'description', group.text)


def extract_credit_card_amount(group: BaseElementGroup, rows: dict):
    elements = group.elements
    if len(group.elements) > 2:
        return
    group.elements.sort(key=lambda e: e.x0)
    if len(elements) == 2 and elements[1].text == 'CR':
        key = 'cash_rebate'
    else:
        key = 'amount'
    try:
        amount = Decimal(elements[0].text.replace(',', ''))
    except decimal.InvalidOperation:
        logging.debug(f'String {elements[0].text} is not a numeric value.')
        return
    prepare_card_dict(rows, group.y0, key, amount)


# Helper method for preparing table rows
def prepare_card_dict(rows: dict, el_y_coor: int, k: str, v: any):
    if el_y_coor not in rows:
        rows[el_y_coor] = {}
    rows[el_y_coor][k] = v
