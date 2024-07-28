from dataclasses import dataclass

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


@dataclass(init=False)
# Create your models here.
class Address(models.Model):
    full_address = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = 'project_address'


@dataclass(init=False)
class FinancialInstitution(models.Model):
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


@dataclass(init=False)
class InstrumentHolder(models.Model):
    full_name = models.CharField(max_length=255)
    address = models.ForeignKey(Address, null=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'project_instrument_holder'
        constraints = [
            models.UniqueConstraint(name='unique_instrument_holder', fields=['full_name', 'address'])
        ]


@dataclass(init=False)
class Statement(models.Model):
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


@dataclass(init=False)
class InstrumentStatement(models.Model):
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


@dataclass(init=False)
class Instrument(models.Model):
    holder = models.ForeignKey(InstrumentHolder, null=True, on_delete=models.SET_NULL)
    provider = models.ForeignKey(FinancialInstitution, null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255)
    number = models.CharField(max_length=20)
    currency = models.CharField(max_length=3)

    class Meta:
        abstract = True


@dataclass(init=False)
class Account(Instrument):
    type = models.CharField(max_length=10)

    class Meta:
        db_table = 'project_account'
        constraints = [
            models.UniqueConstraint(name='unique_account',
                                    fields=['holder', 'provider', 'name', 'number', 'currency', 'type'])
        ]


@dataclass(init=False)
class Card(Instrument):
    class Meta:
        db_table = 'project_card'
        constraints = [
            models.UniqueConstraint(name='unique_card', fields=['holder', 'provider', 'name', 'number', 'currency'])
        ]


@dataclass(init=False)
class Snapshot(models.Model):
    instrument_statement = models.ForeignKey(InstrumentStatement, on_delete=models.CASCADE)

    class Meta:
        abstract = True


@dataclass(init=False)
class AccountSnapshot(Snapshot):
    credit_line = models.DecimalField(max_digits=20, decimal_places=2)
    balance = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        db_table = 'project_account_snapshot'


@dataclass(init=False)
class CardSnapshot(Snapshot):
    total_credit_limit = models.PositiveIntegerField('total credit limit')

    class Meta:
        db_table = 'project_card_snapshot'


@dataclass(init=False)
class Transaction(models.Model):
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


@dataclass(init=False)
class AccountTransaction(Transaction):
    # withdrawals are considered transaction amounts
    deposits = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    balance = models.DecimalField(max_digits=20, decimal_places=2, null=True)

    class Meta:
        db_table = 'project_account_transaction'


@dataclass(init=False)
class CardTransaction(Transaction):
    # transaction date is the date used for base transactions
    post_date = models.DateField('post date', null=True)
    cash_rebate = models.DecimalField(max_digits=20, decimal_places=2, null=True)

    class Meta:
        db_table = 'project_card_transaction'
