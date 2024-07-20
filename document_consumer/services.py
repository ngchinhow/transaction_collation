from pathlib import Path

import django
from pdf_reader import get_elements_from_pdf

django.setup()

from document_consumer.uob.factory import parse_uob_statement


def parse_statement(file_name):
    file_stem = Path(file_name).stem
    pages = get_elements_from_pdf(file_name)
    first_page_elements = pages[0].elements
    first_page_last_line = first_page_elements[len(first_page_elements) - 1].get_text()
    if first_page_last_line.startswith('United Overseas Bank Limited'):
        parse_uob_statement(file_stem, pages, first_page_last_line.split(' • '))


parse_statement('C:\\Users\\AmideWing\\Downloads\\eStatement_17272.506835308788.pdf')
