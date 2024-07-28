import re
from datetime import datetime
from decimal import Decimal
from typing import List, cast

from django.contrib.contenttypes.models import ContentType
from pdf_reader.custom_dataclasses import ExtractedPage, \
    ExtractedTable, \
    PdfParagraph, \
    LineItem, \
    BaseElementGroup, \
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
    instrument_holder_name = ' '.join([word.capitalize() for word in first_page_first_element_words[1:]])

    # Instrument holder address
    first_page_third_element = first_page.elements[2].get_text()
    for item in cast(ExtractedTable, first_page.elements[3]).items:
        first_page_third_element += ' ' + item.base_element_groups.pop().text
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
    account, account_created = Account.objects.get_or_create(type=account_type,
                                                             name=account_name,
                                                             number=account_number,
                                                             holder=holder,
                                                             provider=provider,
                                                             currency=currency)
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
        for element in page.elements:
            if found_end_of_summary and not found_end_of_transactions:
                if isinstance(element, ExtractedTable):
                    table_area = element.table_area
                    transaction_tables.append({
                        'table': element,
                        'table_x_begin_coor': table_area.x0,
                        'table_x_end_coor': table_area.x1,
                        'table_y_begin_coor': table_area.y0,
                        'table_y_end_coor': table_area.y1,
                        'excluded_groups': []
                    })
                else:
                    for table_properties in transaction_tables:
                        if (element.x0 >= table_properties['table_x_begin_coor'] and
                                element.x1 <= table_properties['table_x_end_coor'] and
                                element.y0 >= table_properties['table_y_begin_coor'] and
                                element.y1 <= table_properties['table_y_end_coor']):
                            table_properties['excluded_groups'].append(element.el)

            if (type(element) is ExtractedPdfElement and
                    element.el.text == '----------------------------------------------------------------- End of Summary------------------------------------------------------------'):
                found_end_of_summary = True
            elif (type(element) is ExtractedPdfElement and
                  element.el.text == '------------------------------------------------------------ End of Transaction Details-------------------------------------------------------'):
                found_end_of_transactions = True

        # Create transactions for one account
        for table_properties in transaction_tables:
            transactions_table_items = table_properties['table'].items

            account_number = re.search('.+ ([\\d-]+).*', transactions_table_items[2].el.text).group(1)
            if account_number not in accounts_with_transactions:
                accounts_with_transactions[account_number] = []

            # Table header row
            header_row = transactions_table_items[3]
            header_groups = merge_row_groups(table_properties['excluded_groups'], header_row)
            assert len(header_groups) == 5  # UOB transaction table has 5 columns

            date_x_begin_coor = header_groups[0].x0
            description_x_begin_coor = header_groups[1].x0
            withdrawals_x_end_coor = header_groups[2].x1
            deposits_x_end_coor = header_groups[3].x1
            balance_x_end_coor = header_groups[4].x1

            transaction = None
            transaction_sub_description_rows = []
            # Skip row 5
            for item in transactions_table_items[5:]:
                date = None
                description = ''
                withdrawals = None
                deposits = None
                balance = None
                # Make new transaction
                row_groups = merge_row_groups(table_properties['excluded_groups'], item)
                for group in row_groups:
                    if abs(group.x0 - date_x_begin_coor) < 3:
                        date = datetime.strptime(f'{group.text} {year}', '%d %b %Y')
                    elif abs(group.x0 - description_x_begin_coor) < 3:
                        description = group.text
                    elif abs(group.x1 - withdrawals_x_end_coor) < 3:
                        withdrawals = Decimal(group.text.replace(',', ''))
                    elif abs(group.x1 - deposits_x_end_coor) < 3:
                        deposits = Decimal(group.text.replace(',', ''))
                    elif abs(group.x1 - balance_x_end_coor) < 3:
                        balance = Decimal(group.text.replace(',', ''))

                if balance is not None:
                    if transaction is not None:
                        # Add previous transaction to list
                        add_transaction_to_list(accounts_with_transactions[account_number],
                                                transaction,
                                                account_snapshot_content_type,
                                                transaction_sub_description_rows)
                        transaction_sub_description_rows = []

                    transaction = {
                        'snapshot': account_snapshots[account_number],
                        'date': date,
                        'description': description,
                        'amount': withdrawals,
                        'deposits': deposits,
                        'balance': balance
                    }
                else:
                    # sub-description is contained in the description column
                    transaction_sub_description_rows.append(description)

            # Add last transaction to list
            add_transaction_to_list(accounts_with_transactions[account_number],
                                    transaction,
                                    account_snapshot_content_type,
                                    transaction_sub_description_rows)

    return accounts_with_transactions


def merge_row_groups(excluded_groups: List[BaseElementGroup], item: LineItem):
    groups = list(item.base_element_groups)
    for header_value in item.values:
        if header_value.el is not None:
            groups.append(header_value.el)

    row_y_coor = groups[0].y0
    for excluded_group in list(excluded_groups):
        if abs(excluded_group.y0 - row_y_coor) < 3:
            groups.append(excluded_group)
            excluded_groups.remove(excluded_group)

    groups.sort(key=lambda e: e.x0)

    return groups


def add_transaction_to_list(transactions: List[AccountTransaction],
                            transaction_dict: dict,
                            account_snapshot_content_type: ContentType,
                            sub_description_rows: List[str]):
    account_snapshot = transaction_dict.pop('snapshot')
    sub_description = '\n'.join(sub_description_rows)
    transaction_dict['sub_description'] = sub_description
    transaction, transaction_created = (AccountTransaction.objects
                                        .get_or_create(snapshot_content_type=account_snapshot_content_type,
                                                       snapshot_id=account_snapshot.id,
                                                       row_number=len(transactions) + 1,  # 1 begin list index
                                                       defaults=transaction_dict))
    transactions.append(transaction)
