from typing import List

from pdf_reader.custom_dataclasses import ExtractedPage, ExtractedPdfElement

from components.models import Address, FinancialInstitution
from document_consumer.ocbc.account_parser import parse_ocbc_account_statement
from document_consumer.ocbc.card_parser import parse_ocbc_card_statement


def parse_ocbc_statement(file_name, pages: List[ExtractedPage], fi_info: List[ExtractedPdfElement]):
    full_address = fi_info[1].get_text().replace(',', '') + ' ' + fi_info[2].get_text()
    fi_address, fi_address_created = Address.objects.get_or_create(full_address=full_address)

    fi, fi_created = FinancialInstitution.objects.get_or_create(full_name='Oversea-Chinese Banking Corporation',
                                                                abbreviation=fi_info[0].get_text(),
                                                                address=fi_address,
                                                                company_registration_number='193200032W',
                                                                gst_registration_number='MR-8500130-7',
                                                                website='www.ocbc.com')

    first_page_tenth_element = pages[0].elements[9].get_text()
    last_page_third_last_element = pages[-1].elements[-3].get_text()
    if first_page_tenth_element == 'STATEMENT OF ACCOUNT':
        parse_ocbc_account_statement(file_name, pages, fi)
    elif last_page_third_last_element.endswith('Only requests from Principal Cardmembers are accepted.'):
        parse_ocbc_card_statement(file_name, pages, fi)
