import decimal
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import List, cast

from django.contrib.contenttypes.models import ContentType
from pdf_reader.custom_dataclasses import ExtractedPage, \
    ExtractedTable, \
    PdfParagraph, \
    ExtractedPdfElement

from components.models import FinancialInstitution, \
    Address, \
    InstrumentHolder, \
    Account, \
    AccountTransaction, \
    Statement, \
    InstrumentStatement, AccountSnapshot


def parse_uob_account_statement(file_name, pages: List[ExtractedPage], fi: FinancialInstitution):
    account_snapshots, statement_year = parse_uob_account_metadata(file_name, pages[0], fi)
    accounts_and_transactions = parse_uob_account_transactions(pages[:-1], account_snapshots, statement_year)
    print(account_snapshots)
    print(statement_year)
    print(accounts_and_transactions)


def parse_uob_account_metadata(file_name: str, first_page: ExtractedPage, fi: FinancialInstitution):
    # Instrument holder name
    first_page_first_element_words = first_page.elements[0].get_text().split(' ')
    instrument_holder_name = ' '.join([word.capitalize() for word in first_page_first_element_words if
                                       word not in ['MR', 'MRS', 'MDM', 'MS']])

    # Instrument holder address
    first_page_third_element = first_page.elements[2].get_text()
    for item in cast(ExtractedTable, first_page.elements[3]).items:
        first_page_third_element += ' ' + [group for group in item.base_element_groups if group.text != 'Call'][0].text
    holder_address_text = ' '.join([word.capitalize() for word in first_page_third_element.split(' ')])
    holder_address, holder_address_created = Address.objects.get_or_create(full_address=holder_address_text)
    holder, holder_created = InstrumentHolder.objects.get_or_create(full_name=instrument_holder_name,
                                                                    address=holder_address)

    # Period
    month_end_text = re.search('Account Overview as at (\\d{2} \\w{3} \\d{4})',
                               cast(PdfParagraph, first_page.paragraphs[3]).text).group(1)
    statement_date = datetime.strptime(month_end_text, '%d %b %Y').date()
    statement, statement_created = Statement.objects.get_or_create(holder=holder,
                                                                   provider=fi,
                                                                   date=statement_date,
                                                                   type=Statement.InstrumentType.ACCOUNT,
                                                                   defaults={'file_name': file_name})
    statement_year = statement_date.year

    # Accounts
    i = 4
    account_category = set()
    first_page_paragraphs = first_page.paragraphs
    account_snapshots = {}
    account_content_type = ContentType.objects.get_for_model(Account)
    while i < len(first_page_paragraphs):
        paragraph_i = first_page_paragraphs[i]
        if paragraph_i.get_text() not in account_category:
            # Add text as a new category
            account_category.add(paragraph_i.get_text())
            i += 1
        else:
            account_category.remove(paragraph_i.get_text())
            # Parse currency to balance columns for particular category
            accounts_at_y_coor = parse_uob_account_category_table(holder,
                                                                  fi,
                                                                  cast(ExtractedTable, first_page_paragraphs[i + 1]))
            # Join account details to currency, etc. details using y coordinate
            for j in range(len(accounts_at_y_coor)):
                account_snapshots = (account_snapshots |
                                     merge_uob_account_details(statement,
                                                               account_content_type,
                                                               accounts_at_y_coor,
                                                               cast(PdfParagraph, first_page_paragraphs[i + 2 + j])))
            i += 2 + len(accounts_at_y_coor)

        if not account_category:
            # No more categories to cover
            break

    return account_snapshots, statement_year


def parse_uob_account_category_table(holder: InstrumentHolder,
                                     fi: FinancialInstitution,
                                     account_type_table: ExtractedTable):
    accounts = {}
    currency_x_begin_coor = None
    credit_line_x_end_coor = None
    account_type_table_items = account_type_table.items
    for group in account_type_table_items[0].base_element_groups:
        if group.text == 'Currency':
            currency_x_begin_coor = group.x0
        elif group.text == 'Credit Line':
            credit_line_x_end_coor = group.x1

    for account_table_line in account_type_table_items[1:]:
        line_y_coor = account_table_line.el.y0
        balance = account_table_line.values[0].val_clean
        currency = None
        credit_line = None
        for group in account_table_line.base_element_groups:
            if group.x0 == currency_x_begin_coor:
                currency = group.text
            elif group.x1 == credit_line_x_end_coor:
                credit_line = Decimal(group.text)

        if currency is not None:
            accounts[line_y_coor] = {
                'holder': holder,
                'provider': fi,
                'currency': currency,
                'credit_line': credit_line,
                'balance': balance
            }

    return accounts


def merge_uob_account_details(statement: Statement,
                              account_content_type: ContentType,
                              accounts_at_y_coor: dict,
                              supplement_info: PdfParagraph):
    account_dict = accounts_at_y_coor.pop(supplement_info.elements[1].y0)
    holder = account_dict.pop('holder')
    provider = account_dict.pop('provider')
    currency = account_dict.pop('currency')
    account_type, account_name, account_number = (supplement_info.text
                                                  .split(supplement_info.line_break_char))
    account, account_created = Account.objects.get_or_create(name=account_name,
                                                             number=account_number,
                                                             holder=holder,
                                                             provider=provider,
                                                             defaults={
                                                                 'type': account_type,
                                                                 'currency': currency
                                                             })
    account_statement, account_statement_created = (InstrumentStatement.objects
                                                    .get_or_create(instrument_content_type=account_content_type,
                                                                   instrument_id=account.id,
                                                                   statement=statement))
    account_snapshot, account_snapshot_created = (AccountSnapshot.objects
                                                  .get_or_create(instrument_statement=account_statement,
                                                                 defaults=account_dict))

    return {account_number: account_snapshot}


def parse_uob_account_transactions(pages: List[ExtractedPage], account_snapshots: dict, year: int):
    accounts_with_transactions = {}
    found_end_of_summary = False
    found_end_of_transactions = False
    account_snapshot_content_type = ContentType.objects.get_for_model(AccountSnapshot)

    for page in pages:
        transaction_tables = []
        last_transaction_table = None
        for element in page.elements:
            if found_end_of_summary and not found_end_of_transactions:
                if isinstance(element, ExtractedTable):
                    table_area = element.table_area
                    last_transaction_table = {
                        'table_x_begin_coor': table_area.x0,
                        'table_x_end_coor': table_area.x1,
                        'table_element_groups': {}
                    }

                    # Add groups to dict by y0
                    for item in element.items:
                        y_coor = item.el.y0
                        base_groups = item.base_element_groups
                        for base_group in base_groups:
                            if y_coor not in last_transaction_table['table_element_groups']:
                                last_transaction_table['table_element_groups'][y_coor] = []
                            last_transaction_table['table_element_groups'][y_coor].append(base_group)

                        values = item.values
                        for value in values:
                            if value.el is not None:
                                if value.el.y0 not in last_transaction_table['table_element_groups']:
                                    last_transaction_table['table_element_groups'][value.el.y0] = []
                                last_transaction_table['table_element_groups'][value.el.y0].append(value.el)

                    transaction_tables.append(last_transaction_table)
                elif (last_transaction_table is not None and
                      element.x0 >= last_transaction_table['table_x_begin_coor'] and
                      element.x1 <= last_transaction_table['table_x_end_coor']):
                    if element.y0 not in last_transaction_table['table_element_groups']:
                        last_transaction_table['table_element_groups'][element.y0] = []
                    last_transaction_table['table_element_groups'][element.y0].append(element.el)

            if (type(element) is ExtractedPdfElement and
                    element.el.text == '----------------------------------------------------------------- End of Summary------------------------------------------------------------'):
                found_end_of_summary = True
            elif (type(element) is ExtractedPdfElement and
                  element.el.text == '------------------------------------------------------------ End of Transaction Details-------------------------------------------------------'):
                found_end_of_transactions = True

        # Create transactions for one account
        for table_properties in transaction_tables:
            account_number = None
            account_snapshot = None
            date_x_begin_coor = None
            description_x_begin_coor = None
            withdrawals_x_end_coor = None
            deposits_x_end_coor = None
            balance_x_end_coor = None
            transactions = []
            transaction_table = dict(sorted(table_properties['table_element_groups'].items(),
                                            key=lambda el: el[0],
                                            reverse=True))
            last_y_coor = 0
            last_transaction = None
            for y_coor, element_groups in transaction_table.items():
                # Determine with transaction to use (previous or create a new one)
                if abs(last_y_coor - y_coor) < 3:
                    transaction = last_transaction
                else:
                    transaction = {}

                # Add values to transaction
                for element in element_groups:
                    account_number_match = re.search('^([\\d-]+).*$', element.text)
                    if account_number_match is not None and account_number_match.group(1) in account_snapshots:
                        account_number = account_number_match.group(1)
                        account_snapshot = account_snapshots[account_number]
                        if account_number not in accounts_with_transactions:
                            accounts_with_transactions[account_number] = []
                    elif element.text == 'Date':
                        date_x_begin_coor = element.x0
                    elif element.text == 'Description':
                        description_x_begin_coor = element.x0
                    elif element.text == 'Withdrawals':
                        withdrawals_x_end_coor = element.x1
                    elif element.text == 'Deposits':
                        deposits_x_end_coor = element.x1
                    elif element.text == 'Balance':
                        balance_x_end_coor = element.x1
                    elif date_x_begin_coor is not None and abs(element.x0 - date_x_begin_coor) < 3:
                        transaction['date'] = datetime.strptime(f'{element.text} {year}', '%d %b %Y')
                    elif description_x_begin_coor is not None and abs(element.x0 - description_x_begin_coor) < 3:
                        transaction['description'] = element.text
                    elif withdrawals_x_end_coor is not None and abs(element.x1 - withdrawals_x_end_coor) < 3:
                        try:
                            transaction['amount'] = Decimal(element.text.replace(',', ''))
                        except decimal.InvalidOperation:
                            logging.debug('Text in "Withdrawals" column is not a numeric value')
                    elif deposits_x_end_coor is not None and abs(element.x1 - deposits_x_end_coor) < 3:
                        try:
                            transaction['deposits'] = Decimal(element.text.replace(',', ''))
                        except decimal.InvalidOperation:
                            logging.debug('Text in "Deposits" column is not a numeric value')
                    elif balance_x_end_coor is not None and abs(element.x1 - balance_x_end_coor) < 3:
                        try:
                            transaction['balance'] = Decimal(element.text.replace(',', ''))
                        except decimal.InvalidOperation:
                            logging.debug('Text in "Balance" column is not a numeric value')

                # Determine row or sub row
                if 'balance' not in transaction:
                    if last_transaction is not None:
                        if 'sub_description' not in last_transaction:
                            last_transaction['sub_description'] = []
                        last_transaction['sub_description'].append(transaction['description'])
                else:
                    last_transaction = transaction
                    transactions.append(last_transaction)

            for transaction in transactions:
                # Add last transaction to list
                add_transaction_to_list(accounts_with_transactions[account_number],
                                        transaction,
                                        account_snapshot,
                                        account_snapshot_content_type)

    return accounts_with_transactions


def add_transaction_to_list(transactions: List[AccountTransaction],
                            transaction_dict: dict,
                            account_snapshot: AccountSnapshot,
                            account_snapshot_content_type: ContentType):
    if 'sub_description' in transaction_dict:
        transaction_dict['sub_description'] = '\n'.join(transaction_dict['sub_description'])
    transaction, transaction_created = (AccountTransaction.objects
                                        .get_or_create(snapshot_content_type=account_snapshot_content_type,
                                                       snapshot_id=account_snapshot.id,
                                                       row_number=len(transactions) + 1,  # 1 begin list index
                                                       defaults=transaction_dict))
    transactions.append(transaction)
