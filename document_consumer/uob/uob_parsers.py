from datetime import datetime
from components.models import FinancialInstitution, Address, InstrumentHolder


def parse_uob_statement(pages, fi_information):
    fi_address = Address(full_address=fi_information[1])
    fi = FinancialInstitution(full_name=fi_information[0],
                              abbreviation='UOB',
                              address=fi_address,
                              company_registration_number=fi_information[2].replace('Co. Reg. No. ', ''),
                              gst_registration_number=fi_information[3].replace('GST Reg. No. ', ''),
                              email=fi_information[4])

    print(pages[0].paragraphs[2])
    first_page_second_paragraph_elements = pages[0].paragraphs[2].elements
    print(first_page_second_paragraph_elements)
    if first_page_second_paragraph_elements[0].get_text() == 'Statement of Account':
        parse_uob_account_statement(pages, fi, first_page_second_paragraph_elements[1].get_text())


def parse_uob_account_statement(pages, fi: FinancialInstitution, period: str):
    first_page_first_element_words = pages[0].elements[0].get_text().split(' ')
    instrument_holder_name = ' '.join([word.capitalize() for word in first_page_first_element_words[1:]])

    first_page_third_element = pages[0].elements[2].get_text()
    for item in pages[0].elements[3].items:
        first_page_third_element += ' ' + item.el.text.replace(' Call', '')
    instrument_holder_address_text = ' '.join([word.capitalize() for word in first_page_third_element.split(' ')])
    instrument_holder_address = Address(full_address=instrument_holder_address_text)
    instrument_holder = InstrumentHolder(full_name=instrument_holder_name, address=instrument_holder_address)

    import re
    period_search = re.search('Period: .+ to (\\d{2} \\w{3} \\d{4})', period)
    period_end = datetime.strptime(period_search.group(1), '%d %b %Y').date()
    print(period_end)
