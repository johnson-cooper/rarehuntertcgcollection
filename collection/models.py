from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
import os
from django.conf import settings

class Order(models.Model):
    stripe_order_id = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    status = models.CharField(max_length=50, default='paid')  # paid, shipped, etc.
    shipping_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.JSONField(blank=True, null=True)
    items = models.JSONField()  # list of line items: description + quantity
    tracking_number = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order {self.stripe_order_id} ({self.email})"

class CollectionImport(models.Model):
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file = models.FileField(upload_to="imports/")
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Import @ {self.uploaded_at}"

class CardSet(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    release_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name

class Card(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    konami_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    def __str__(self):
        return self.name

class ImportBatch(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    file = models.FileField(upload_to='imports/', null=True, blank=True)
    mode = models.CharField(max_length=20, default='merge', choices=(('merge','Merge'), ('replace','Replace')))

    def __str__(self):
        return f"{self.name} @ {self.uploaded_at.isoformat()} ({self.mode})"

class CollectionCard(models.Model):
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name='collection_entries')
    card_set = models.ForeignKey(CardSet, on_delete=models.SET_NULL, null=True, blank=True, related_name='collection_entries')
    edition = models.CharField(max_length=50, default='Unlimited', blank=True)
    condition = models.CharField(max_length=20, blank=True)
    quantity = models.IntegerField(default=1)
    reserved = models.IntegerField(default=0)
    misprint = models.TextField(null=True, blank=True)
    psa = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    value_low = models.FloatField(null=True, blank=True)
    value_mid = models.FloatField(null=True, blank=True)
    value_high = models.FloatField(null=True, blank=True)
    effective_mid = models.FloatField(null=True, blank=True)
    pricing_source = models.CharField(max_length=64, null=True, blank=True)

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='cards')
    exported_id = models.IntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

        # optional: helper property for available stock
    @property
    def available(self):
        return self.quantity - self.reserved

class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=["exported_id"],
            condition=models.Q(exported_id__isnull=False),
            name="uq_exported_card_global"
        )
    ]

    def __str__(self):
        return f"{self.card.name} ({self.card_set}) - {self.edition}"

class CollectionImage(models.Model):
    collection_card = models.ForeignKey(
        CollectionCard,
        on_delete=models.CASCADE,
        related_name='images'
    )
    img = models.ImageField(upload_to='collection_images/')  # <-- changed from CharField

    def __str__(self):
        return f"Image for {self.collection_card}"


@receiver(post_delete, sender=CollectionImage)
def delete_image_file(sender, instance, **kwargs):
    """Delete the image file from disk when CollectionImage is deleted."""
    if instance.img:
        img_path = os.path.join(settings.MEDIA_ROOT, str(instance.img))
        if os.path.exists(img_path):
            os.remove(img_path)

