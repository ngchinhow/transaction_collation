import re
from datetime import datetime
from typing import List

from django.contrib.contenttypes.models import ContentType
from pdf_reader.custom_dataclasses import ExtractedPage, ExtractedPdfElement

from components.models import FinancialInstitution, \
    Address, \
    InstrumentHolder, \
    Statement, \
    Account, \
    InstrumentStatement


def parse_ocbc_account_statement(file_name, pages: List[ExtractedPage], fi: FinancialInstitution):
    statement, account_element_index = parse_ocbc_account_metadata(file_name, pages[0], fi)
    parse_ocbc_account_transactions(pages, statement, account_element_index)


def parse_ocbc_account_metadata(file_name, first_page: ExtractedPage, fi: FinancialInstitution):
    holder_info = first_page.paragraphs[2].get_text().split('\n')

    holder_name_text = ' '.join([word.capitalize() for word in holder_info.pop(0).split(' ')])

    # Instrument holder address
    holder_address_text = ' '.join([' '.join([word.capitalize() for word in row.split(' ')]) for row in holder_info])
    holder_address, holder_address_created = Address.objects.get_or_create(full_address=holder_address_text)

    # Instrument holder name
    holder, holder_created = InstrumentHolder.objects.get_or_create(full_name=holder_name_text, address=holder_address)

    # Period
    account_element_index = None
    statement_date = None
    period_pattern = '^\\d{1,2} \\w{3} \\d{4} TO (\\d{1,2} \\w{3} \\d{4})$'
    for i, element in enumerate(first_page.elements):
        if match := re.fullmatch(period_pattern, element.get_text()):
            account_element_index = i - 1
            statement_date = datetime.strptime(match.group(1), '%d %b %Y').date()
            break

    # Statement
    statement, statement_created = Statement.objects.get_or_create(holder=holder,
                                                                   provider=fi,
                                                                   date=statement_date,
                                                                   type=Statement.InstrumentType.ACCOUNT,
                                                                   defaults={'file_name': file_name})

    return statement, account_element_index


def parse_ocbc_account_transactions(pages: List[ExtractedPage], statement: Statement, account_element_index: int):
    account_numbers_with_transactions = {}
    is_end_of_transactions = False
    account_number_pattern = '^Account No. (\\d+)$'
    account_ct = ContentType.objects.get_for_model(Account)
    last_account_statement = None

    for i, page in enumerate(pages):
        j = account_element_index if i == 0 else 0
        elements = page.elements
        transaction_x_coor = None
        description_x_coor = None
        withdrawal_x_coor = None
        deposit_x_coor = None
        balance_x_coor = None

        while j < len(elements) and not is_end_of_transactions:
            element = elements[j]
            element_text = element.get_text()
            if isinstance(element, ExtractedPdfElement) and element_text == 'CHECK YOUR STATEMENT':
                is_end_of_transactions = True
            elif isinstance(element, ExtractedPdfElement) and (match := re.fullmatch(account_number_pattern,
                                                                                     element_text)):
                # Make account and account statement
                account_number = match.group(1)
                account_name = elements[j - 2].get_text()
                account, account_created = Account.objects.get_or_create(name=account_name,
                                                                         number=account_number,
                                                                         holder=statement.holder,
                                                                         provider=statement.provider)
                account_statement, account_statement_created = (InstrumentStatement.objects
                                                                .get_or_create(instrument_content_type=account_ct,
                                                                               instrument_id=account.id,
                                                                               statement=statement))
                last_account_statement = account_statement


        if is_end_of_transactions:
            break
