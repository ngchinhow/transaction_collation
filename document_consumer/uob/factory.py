from typing import List, cast

from pdf_reader.custom_dataclasses import ExtractedPage, PdfParagraph

from components.models import Address, FinancialInstitution
from document_consumer.uob.account_parser import parse_uob_account_statement
from document_consumer.uob.card_parser import parse_uob_card_statement


def parse_uob_statement(file_name, pages: List[ExtractedPage], fi_information):
    fi_address, fi_address_created = Address.objects.get_or_create(full_address=fi_information[1])

    company_registration_number = fi_information[2].replace('Co. Reg. No. ', '')
    gst_registration_number = fi_information[3].replace('GST Reg. No. ', '')
    fi, fi_created = FinancialInstitution.objects.get_or_create(full_name=fi_information[0],
                                                                abbreviation='UOB',
                                                                address=fi_address,
                                                                company_registration_number=company_registration_number,
                                                                gst_registration_number=gst_registration_number,
                                                                email=fi_information[4])

    first_page_second_paragraph_first_element_text = cast(PdfParagraph, pages[0].paragraphs[2]).elements[0].get_text()
    match first_page_second_paragraph_first_element_text:
        case 'Statement of Account':
            parse_uob_account_statement(file_name, pages, fi)
        case 'Credit Card(s) Statement':
            parse_uob_card_statement(file_name, pages, fi)
