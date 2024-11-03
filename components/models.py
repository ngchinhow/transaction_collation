from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


class LoggableModel(models.Model):
    def __repr__(self):
        return repr({k: str(v) for k, v in vars(self).items() if k != '_state'})

    class Meta:
        abstract = True


class Address(LoggableModel):
    full_address = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = 'project_address'


class FinancialInstitution(LoggableModel):
    full_name = models.CharField(max_length=255, null=True)
    abbreviation = models.CharField(max_length=5)
    address = models.ForeignKey(Address, null=True, on_delete=models.SET_NULL)
    company_registration_number = models.CharField(max_length=20, null=True)
    gst_registration_number = models.CharField(max_length=20, null=True)
    website = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = 'project_financial_institution'
        constraints = [
            models.UniqueConstraint(name='unique_financial_institution', fields=['full_name', 'abbreviation'])
        ]


class InstrumentHolder(LoggableModel):
    full_name = models.CharField(max_length=255)
    address = models.ForeignKey(Address, null=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'project_instrument_holder'
        constraints = [
            models.UniqueConstraint(name='unique_instrument_holder', fields=['full_name', 'address'])
        ]


class Statement(LoggableModel):
    class InstrumentType(models.TextChoices):
        ACCOUNT = 'ACCOUNT', _('Account')
        CARD = 'CARD', _('Card')

    holder = models.ForeignKey(InstrumentHolder, null=True, on_delete=models.SET_NULL)
    provider = models.ForeignKey(FinancialInstitution, null=True, on_delete=models.SET_NULL)
    file_name = models.CharField(max_length=255, unique=True)
    date = models.DateField('statement date')
    type = models.CharField(max_length=10, choices=InstrumentType)

    class Meta:
        db_table = 'project_statement'
        constraints = [
            models.UniqueConstraint(name='unique_statement', fields=['holder', 'provider', 'date', 'type'])
        ]


class InstrumentStatement(LoggableModel):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE)
    instrument_content_type = models.ForeignKey(ContentType, null=True, on_delete=models.SET_NULL)
    instrument_id = models.PositiveIntegerField()
    instrument = GenericForeignKey('instrument_content_type', 'instrument_id')

    class Meta:
        db_table = 'project_instrument_statement'
        constraints = [
            models.UniqueConstraint(name='unique_instrument_statement',
                                    fields=['instrument_content_type', 'instrument_id', 'statement'])
        ]


class Instrument(LoggableModel):
    holder = models.ForeignKey(InstrumentHolder, null=True, on_delete=models.SET_NULL)
    provider = models.ForeignKey(FinancialInstitution, null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255)
    number = models.CharField(max_length=20)
    currency = models.CharField(max_length=3, null=True)

    class Meta:
        abstract = True


class Account(Instrument):
    type = models.CharField(max_length=10, null=True)

    class Meta:
        db_table = 'project_account'
        constraints = [
            models.UniqueConstraint(name='unique_account',
                                    fields=['holder', 'provider', 'name', 'number'])
        ]


class Card(Instrument):
    parent = models.ForeignKey("self", null=True, on_delete=models.SET_NULL)
    name_on_card = models.CharField(max_length=255)

    class Meta:
        db_table = 'project_card'
        constraints = [
            models.UniqueConstraint(name='unique_card',
                                    fields=['holder', 'provider', 'name', 'name_on_card', 'number', 'currency'])
        ]


class Snapshot(LoggableModel):
    instrument_statement = models.ForeignKey(InstrumentStatement, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class AccountSnapshot(Snapshot):
    credit_line = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    balance = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        db_table = 'project_account_snapshot'


class CardSnapshot(Snapshot):
    total_credit_limit = models.PositiveIntegerField('total credit limit')

    class Meta:
        db_table = 'project_card_snapshot'


class Transaction(LoggableModel):
    date = models.DateField('transaction date', null=True)
    description = models.CharField(max_length=255)
    sub_description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    row_number = models.IntegerField('row number in corresponding table in statement')
    snapshot_content_type = models.ForeignKey(ContentType, null=True, on_delete=models.SET_NULL)
    snapshot_id = models.PositiveIntegerField()
    snapshot = GenericForeignKey('snapshot_content_type', 'snapshot_id')

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(name='unique_%(class)',
                                    fields=['snapshot_content_type', 'snapshot_id', 'row_number'])
        ]


class AccountTransaction(Transaction):
    # withdrawals are considered transaction amounts
    deposits = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    balance = models.DecimalField(max_digits=20, decimal_places=2, null=True)

    class Meta:
        db_table = 'project_account_transaction'


class CardTransaction(Transaction):
    # transaction date is the date used for base transactions
    post_date = models.DateField('post date', null=True)
    cash_rebate = models.DecimalField(max_digits=20, decimal_places=2, null=True)

    class Meta:
        db_table = 'project_card_transaction'
