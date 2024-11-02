import csv
from pathlib import Path

import django
import pytesseract.pytesseract
from pdf_reader import get_elements_from_pdf


django.setup()

from document_consumer.ocbc.factory import parse_ocbc_statement
from document_consumer.uob.factory import parse_uob_statement
from document_consumer.posb.account_parser import parse_posb_account_transactions
from components.models import InstrumentHolder


def parse_statement(file_name):
    file = Path(file_name)
    file_extension = file.suffix
    file_stem = file.stem
    pytesseract.pytesseract.tesseract_cmd = \
        'C:\\Users\\AmideWing\\AppData\\Local\\Programs\\Tesseract-OCR\\tesseract.exe'
    match file_extension.casefold():
        case '.pdf':
            pages = get_elements_from_pdf(file_name)
            first_page_elements = pages[0].elements
            first_page_first_line = first_page_elements[0].get_text()
            first_page_last_line = first_page_elements[len(first_page_elements) - 1].get_text()
            if first_page_first_line == 'OCBC Bank':
                parse_ocbc_statement(file_stem, pages, first_page_elements[0:3])
            elif first_page_last_line.startswith('United Overseas Bank Limited'):
                parse_uob_statement(file_stem, pages, first_page_last_line.split(' • '))
        case '.csv':
            with open(file_name, 'r') as csvfile:
                csvreader = csv.reader(csvfile)
                collected_rows = [row for row in csvreader if row]

            if collected_rows[0][1].startswith('POSB'):
                parse_posb_account_transactions(file_stem, InstrumentHolder.objects.get(pk=1), 'SGD', collected_rows)


parse_statement('C:\\Users\\AmideWing\\Downloads\\Telegram Desktop\\eStatement_3589.8688804110357.pdf')
