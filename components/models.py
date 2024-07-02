from dataclasses import dataclass
from django.db import models


@dataclass(init=False)
# Create your models here.
class Address(models.Model):
    full_address = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = 'project_address'


@dataclass(init=False)
class FinancialInstitution(models.Model):
    full_name = models.CharField(max_length=255)
    abbreviation = models.CharField(max_length=5)
    address = models.ForeignKey(Address, on_delete=models.DO_NOTHING)
    company_registration_number = models.CharField(max_length=20)
    gst_registration_number = models.CharField(max_length=20)
    email = models.EmailField(max_length=255)

    class Meta:
        db_table = 'project_financial_institution'
        constraints = [
            models.UniqueConstraint(name='unique_financial_institution',
                                    fields=['full_name', 'address', 'company_registration_number',
                                            'gst_registration_number', 'email'])
        ]


@dataclass(init=False)
class InstrumentHolder(models.Model):
    full_name = models.CharField(max_length=255)
    address = models.ForeignKey(Address, on_delete=models.DO_NOTHING)

    class Meta:
        db_table = 'project_instrument_holder'
        constraints = [
            models.UniqueConstraint(name='unique_instrument_holder', fields=['full_name', 'address'])
        ]


@dataclass(init=False)
class Instrument(models.Model):
    holder = models.ForeignKey(InstrumentHolder, on_delete=models.DO_NOTHING)
    provider = models.ForeignKey(FinancialInstitution, on_delete=models.DO_NOTHING)
    name = models.CharField(max_length=255)
    number = models.CharField(max_length=20)
    currency = models.CharField(max_length=3)

    class Meta:
        abstract = True


@dataclass(init=False)
class Account(Instrument):
    type = models.CharField(max_length=10)
    credit_line = models.DecimalField(max_digits=20, decimal_places=2)
    balance = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        db_table = 'project_account'
        constraints = [
            models.UniqueConstraint(name='unique_account',
                                    fields=['holder', 'provider', 'name', 'number', 'currency', 'type'])
        ]


@dataclass(init=False)
class Card(Instrument):
    total_credit_limit = models.PositiveIntegerField('total credit limit')

    class Meta:
        db_table = 'project_card'
        constraints = [
            models.UniqueConstraint(name='unique_card', fields=['holder', 'provider', 'name', 'number', 'currency'])
        ]


@dataclass(init=False)
class Transaction(models.Model):
    date = models.DateField('transaction date', null=True)
    description = models.CharField(max_length=255)
    sub_description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    row_number = models.IntegerField('row number in corresponding table in statement', null=True)
    file_name = models.CharField(max_length=255)

    class Meta:
        abstract = True


@dataclass(init=False)
class AccountTransaction(Transaction):
    account = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    # withdrawals are considered transaction amounts
    deposits = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    balance = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        db_table = 'project_account_transaction'
        constraints = [
            models.UniqueConstraint(name='unique_account_transaction', fields=['file_name', 'account', 'row_number'])
        ]


@dataclass(init=False)
class CardTransaction(Transaction):
    card = models.ForeignKey(Card, on_delete=models.DO_NOTHING)
    # transaction date is the date used for base transactions
    post_date = models.DateField('post date', null=True)
    cash_rebate = models.DecimalField(max_digits=20, decimal_places=2, null=True)

    class Meta:
        db_table = 'project_card_transaction'
        constraints = [
            models.UniqueConstraint(name='unique_card_transaction', fields=['file_name', 'card', 'row_number'])
        ]
