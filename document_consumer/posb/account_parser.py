import re
from datetime import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from components.models import FinancialInstitution, \
    InstrumentHolder, \
    Account, \
    Statement, \
    InstrumentStatement, \
    AccountSnapshot, AccountTransaction


def parse_posb_account_transactions(file_name: str, holder: InstrumentHolder, rows: list):
    # Financial institution and account
    account_details = re.search('^(\\w+) ([\\w\\s]+?) (\\w+) Account ([\\d-]+)$', rows[0][1])
    fi, fi_created = FinancialInstitution.objects.get_or_create(abbreviation=account_details.group(1))
    account_content_type = ContentType.objects.get_for_model(Account)
    account, account_created = Account.objects.get_or_create(holder=holder,
                                                             provider=fi,
                                                             name=account_details.group(2),
                                                             number=account_details.group(4),
                                                             defaults={
                                                                 'type': account_details.group(3)
                                                             })

    # Statement
    statement_date = datetime.strptime(rows[1][1].strip(), '%d %b %Y').date()
    statement, statement_created = Statement.objects.get_or_create(holder=holder,
                                                                   provider=fi,
                                                                   file_name=file_name,
                                                                   date=statement_date,
                                                                   type=Statement.InstrumentType.ACCOUNT)

    # Instrument statement and account snapshot
    balance = Decimal(rows[2][1])
    account_statement, account_statement_created = (InstrumentStatement.objects
                                                    .get_or_create(instrument_content_type=account_content_type,
                                                                   instrument_id=account.id,
                                                                   statement=statement))
    account_snapshot, account_snapshot_created = (AccountSnapshot.objects
                                                  .get_or_create(instrument_statement=account_statement,
                                                                 defaults={
                                                                     'credit_line': Decimal(0),
                                                                     'balance': balance
                                                                 }))

    # Transactions
    account_snapshot_content_type = ContentType.objects.get_for_model(AccountSnapshot)

    for i, row in enumerate(rows[5:]):
        transaction_date = datetime.strptime(row[0], '%d %b %Y').date()
        debit = Decimal(row[2]) if row[2].strip() != '' else None
        credit = Decimal(row[3]) if row[3].strip() != '' else None
        description = row[4].strip()
        sub_description = '\n'.join([text.strip() for text in row[5:] if text.strip() != ''])

        transaction, transaction_created = (AccountTransaction.objects
                                            .get_or_create(snapshot_content_type=account_snapshot_content_type,
                                                           snapshot_id=account_snapshot.id,
                                                           row_number=i + 1,  # 1 begin list index
                                                           defaults={
                                                               'date': transaction_date,
                                                               'description': description,
                                                               'sub_description': sub_description,
                                                               'amount': debit,
                                                               'deposits': credit
                                                           }))
