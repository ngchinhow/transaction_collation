import django
from pdf_reader import get_elements_from_pdf

django.setup()


def parse_statement(file_name):
    pages = get_elements_from_pdf(file_name)
    first_page_elements = pages[0].elements
    first_page_last_line = first_page_elements[len(first_page_elements) - 1].get_text()
    if first_page_last_line.startswith('United Overseas Bank Limited'):
        from document_consumer.uob.uob_parsers import parse_uob_statement
        parse_uob_statement(pages, first_page_last_line.split(' • '))


parse_statement('C:\\Users\\AmideWing\\Downloads\\eStatement_50419.14378413525.pdf')
