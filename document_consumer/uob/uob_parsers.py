from datetime import datetime
from typing import List, cast

from pdf_reader.custom_dataclasses import ExtractedPage, ExtractedTable, PdfParagraph

from components.models import FinancialInstitution, Address, InstrumentHolder, Account


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
    print(accounts)
    print(statement_year)


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
                from decimal import Decimal
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
    import re
    period_search = re.search('Period: .+ to (\\d{2} \\w{3} \\d{4})', period)
    statement_year = datetime.strptime(period_search.group(1), '%d %b %Y').date().year

    return accounts, statement_year
