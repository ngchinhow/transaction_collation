import re
from datetime import datetime
from decimal import Decimal
from typing import List, cast

from pdf_reader.custom_dataclasses import ExtractedPage, ExtractedTable, PdfParagraph, LineItem, BaseElementGroup

from components.models import FinancialInstitution, Address, InstrumentHolder, Account, AccountTransaction, Transaction


def parse_uob_statement(pages: List[ExtractedPage], fi_information):
    fi_address = Address(full_address=fi_information[1])
    fi = FinancialInstitution(full_name=fi_information[0],
                              abbreviation='UOB',
                              address=fi_address,
                              company_registration_number=fi_information[2].replace('Co. Reg. No. ', ''),
                              gst_registration_number=fi_information[3].replace('GST Reg. No. ', ''),
                              email=fi_information[4])

    first_page_second_paragraph_elements = cast(PdfParagraph, pages[0].paragraphs[2]).elements
    if first_page_second_paragraph_elements[0].get_text() == 'Statement of Account':
        parse_uob_account_statement(pages, fi, first_page_second_paragraph_elements[1].get_text())


def parse_uob_account_statement(pages: List[ExtractedPage], fi: FinancialInstitution, period: str):
    accounts, statement_year = parse_uob_account_metadata(pages[0], fi, period)
    accounts_and_transactions = parse_uob_account_transactions(pages[1:-1], accounts, statement_year)
    print(accounts)
    print(statement_year)
    print(accounts_and_transactions)


def parse_uob_account_metadata(first_page: ExtractedPage, fi: FinancialInstitution, period: str):
    # Instrument holder name
    first_page_first_element_words = first_page.elements[0].get_text().split(' ')
    instrument_holder_name = ' '.join([word.capitalize() for word in first_page_first_element_words[1:]])

    # Instrument holder address
    first_page_third_element = first_page.elements[2].get_text()
    for item in cast(ExtractedTable, first_page.elements[3]).items:
        first_page_third_element += ' ' + item.el.text.replace(' Call', '')
    instrument_holder_address_text = ' '.join([word.capitalize() for word in first_page_third_element.split(' ')])
    instrument_holder_address = Address(full_address=instrument_holder_address_text)
    instrument_holder = InstrumentHolder(full_name=instrument_holder_name, address=instrument_holder_address)

    # Accounts
    first_page_seventh_paragraph_items = cast(ExtractedTable, first_page.paragraphs[6]).items
    currency_x_begin_coor = None
    credit_line_x_end_coor = None
    for group in first_page_seventh_paragraph_items[0].base_element_groups:
        if group.text == 'Currency':
            currency_x_begin_coor = group.x0
        elif group.text == 'Credit Line':
            credit_line_x_end_coor = group.x1

    accounts = {}
    for account_table_line in first_page_seventh_paragraph_items[1:]:
        line_y_coor = account_table_line.el.y0
        balance = account_table_line.values[0]
        currency = None
        credit_line = None
        for group in account_table_line.base_element_groups:
            if group.x0 == currency_x_begin_coor:
                currency = group.text
            elif group.x1 == credit_line_x_end_coor:
                credit_line = Decimal(group.text)

        if currency is not None:
            accounts[line_y_coor] = Account(holder=instrument_holder,
                                            provider=fi,
                                            currency=currency,
                                            credit_line=credit_line,
                                            balance=balance)

    for account_detail_paragraph in first_page.paragraphs[7:7 + len(accounts)]:
        account_detail_paragraph = cast(PdfParagraph, account_detail_paragraph)
        account = accounts.pop(account_detail_paragraph.elements[1].y0)
        account_type, account_name, account_number = (account_detail_paragraph.text
                                                      .split(account_detail_paragraph.line_break_char))
        account.type = account_type
        account.name = account_name
        account.number = account_number

        accounts[account_number] = account

    # Statement year
    period_search = re.search('Period: .+ to (\\d{2} \\w{3} \\d{4})', period)
    statement_year = datetime.strptime(period_search.group(1), '%d %b %Y').date().year

    return accounts, statement_year


def parse_uob_account_transactions(pages: List[ExtractedPage], accounts: dict, year: int):
    accounts_with_transactions = {}
    for page in pages:
        transaction_table = cast(ExtractedTable, page.elements[0])
        transaction_table_area = transaction_table.table_area
        transaction_table_x_begin_coor = transaction_table_area.x0
        transaction_table_x_end_coor = transaction_table_area.x1
        transaction_table_y_begin_coor = transaction_table_area.y0
        transaction_table_y_end_coor = transaction_table_area.y1

        excluded_table_base_element_groups: List[BaseElementGroup] = []
        for page_element in page.elements[1:]:
            if (page_element.x0 >= transaction_table_x_begin_coor and
                    page_element.x1 <= transaction_table_x_end_coor and
                    page_element.y0 >= transaction_table_y_begin_coor and
                    page_element.y1 <= transaction_table_y_end_coor):
                excluded_table_base_element_groups.append(cast(BaseElementGroup, page_element.el))

        transactions_table_items = transaction_table.items

        account_number = re.search('.+ ([\\d-]+).*', transactions_table_items[2].el.text).group(1)
        if account_number not in accounts_with_transactions:
            accounts_with_transactions[account_number] = []

        # Table header row
        header_row = transactions_table_items[3]
        header_groups = merge_row_groups(excluded_table_base_element_groups, header_row)
        assert len(header_groups) == 5  # UOB transaction table has 5 columns

        date_x_begin_coor = header_groups[0].x0
        description_x_begin_coor = header_groups[1].x0
        withdrawals_x_end_coor = header_groups[2].x1
        deposits_x_end_coor = header_groups[3].x1
        balance_x_end_coor = header_groups[4].x1

        account_transaction = None
        transaction_sub_description_rows = []
        # Skip row 5
        for item in transactions_table_items[5:]:
            date = None
            description = ''
            withdrawals = None
            deposits = None
            balance = None
            # Make new transaction
            row_groups = merge_row_groups(excluded_table_base_element_groups, item)
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
                if account_transaction is not None:
                    # Add previous transaction to list
                    add_transaction_to_list(accounts_with_transactions[account_number],
                                            account_transaction,
                                            transaction_sub_description_rows)
                    transaction_sub_description_rows = []

                account_transaction = AccountTransaction(instrument=accounts[account_number],
                                                         date=date,
                                                         description=description,
                                                         amount=withdrawals,
                                                         deposits=deposits,
                                                         balance=balance)
            else:
                # sub-description is contained in the description column
                transaction_sub_description_rows.append(description)

        # Add last transaction to list
        add_transaction_to_list(accounts_with_transactions[account_number],
                                account_transaction,
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


def add_transaction_to_list(transactions: List[Transaction], transaction: Transaction, sub_description_rows: List[str]):
    transaction.sub_description = '\n'.join(sub_description_rows)
    transactions.append(transaction)
