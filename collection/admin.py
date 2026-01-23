from django.contrib import admin, messages
from django import forms
import json, zipfile, os, tempfile
from django.db import transaction
from django.core.files import File
from django.conf import settings
from .models import Card, CardSet, CollectionCard, ImportBatch, CollectionImage, Order
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = "mail.smtp2go.com"
SMTP_PORT = 2525  # could also be 587, 8025, or 25
SMTP_USERNAME = settings.SMTP2GO_USERNAME
SMTP_PASSWORD = settings.SMTP2GO_PASSWORD
FROM_EMAIL = "orders@rarehuntertcg.com"

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('stripe_order_id','email','status','tracking_number','created_at')
    readonly_fields = ('stripe_order_id','email','items','shipping_name','shipping_address')
    actions = ['send_tracking_email']

    def send_tracking_email(self, request, queryset):
        for order in queryset:
            if order.tracking_number and order.status != 'shipped':
                # --- build email ---
                msg = MIMEMultipart('mixed')
                msg['Subject'] = f"Your Rare Hunter TCG Order #{order.stripe_order_id} Has Shipped!"
                msg['From'] = FROM_EMAIL
                msg['To'] = order.email

                # plain text
                text = f"""
Hi {order.email},

Your Rare Hunter TCG order #{order.stripe_order_id} has shipped.
Tracking Number: {order.tracking_number}

Thank you for shopping with us!
"""
                # optional HTML version
                html = f"""
<html>
  <body>
    <p>Hi {order.email},</p>
    <p>Your Rare Hunter TCG order <strong>#{order.stripe_order_id}</strong> has shipped.</p>
    <p>Tracking Number: <strong>{order.tracking_number}</strong></p>
    <p>Thank you for shopping with us!</p>
  </body>
</html>
"""
                msg.attach(MIMEText(text, 'plain'))
                msg.attach(MIMEText(html, 'html'))

                # --- send via SMTP2GO ---
                try:
                    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()
                        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
                        smtp.sendmail(FROM_EMAIL, [order.email], msg.as_string())
                    order.status = 'shipped'
                    order.save()
                    self.message_user(request, f"Sent tracking email to {order.email}")
                except Exception as e:
                    self.message_user(request, f"Failed to send to {order.email}: {e}", level='error')

    send_tracking_email.short_description = "Send tracking emails for selected orders"

class ImportBatchForm(forms.ModelForm):
    upload_zip = forms.FileField(
        required=False, 
        help_text='Upload a ZIP containing JSON + images'
    )

    class Meta:
        model = ImportBatch
        fields = ['name', 'exported_at', 'mode', 'upload_zip']

@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    form = ImportBatchForm
    list_display = ('name','uploaded_at','exported_at','mode')
    readonly_fields = ('uploaded_at',)

    def save_model(self, request, obj, form, change):
        upload = form.cleaned_data.get('upload_zip')
        super().save_model(request, obj, form, change)

        if not upload:
            return

        obj.file.save(upload.name, upload, save=True)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Extract ZIP
                with zipfile.ZipFile(upload) as zf:
                    zf.extractall(tmpdir)

                # Locate JSON
                json_file = [f for f in os.listdir(tmpdir) if f.endswith('.json')]
                if not json_file:
                    self.message_user(request, 'No JSON file found in ZIP', level=messages.ERROR)
                    return

                json_path = os.path.join(tmpdir, json_file[0])
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Import cards + images in a transaction
                with transaction.atomic():
                    created, updated, deleted = self.import_zip_data(obj, data, tmpdir)

                self.message_user(
                    request, 
                    f'Import complete: {created} created, {updated} updated, {deleted} deleted', 
                    level=messages.SUCCESS
                )
        except Exception as e:
            self.message_user(request, f'Import failed: {e}', level=messages.ERROR)
            raise

    def import_zip_data(self, batch, data, tmpdir):
        """
        Replace-capable importer:
        - If batch.mode == 'replace' -> delete ALL CollectionImage files and CollectionCard rows first
        - Then recreate CollectionCard rows from JSON and save images under MEDIA_ROOT with the same basename
        - Merge mode behaves as before (only updates/creates)
        """
        created = updated = deleted = 0
        incoming_ids = set()
        is_replace = batch.mode == 'replace'

        # ---------- FULL TABLE WIPE for REPLACE ----------
        if is_replace:
            # Delete all image files and image rows
            for img in CollectionImage.objects.all():
                try:
                    path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                img.delete()

            # Delete all collection cards
            deleted = CollectionCard.objects.count()
            CollectionCard.objects.all().delete()

        # ---------- IMPORT LOOP ----------
        for c in data.get('cards', []):
            exported_id = c.get('id')
            incoming_ids.add(exported_id)

            # --- Card ---
            card_obj, _ = Card.objects.get_or_create(
                konami_id=c.get('konami_id'),
                defaults={'name': c.get('name') or ''}
            )

            # --- CardSet ---
            set_data = c.get('set')
            card_set = None
            if set_data:
                card_set, _ = CardSet.objects.get_or_create(
                    code=set_data.get('code'),
                    defaults={
                        'name': set_data.get('name'),
                        'release_date': set_data.get('release_date')
                    }
                )

            # --- CollectionCard ---
            # In replace mode the table was just cleared so this will be None and we'll create a new row.
            coll_card = CollectionCard.objects.filter(
                exported_id=exported_id,
                import_batch=batch
            ).first()

            edition = c.get('edition', 'Unlimited')
            condition = c.get('condition')
            quantity = c.get('quantity', 1)
            misprint = (c.get('misprint', {}).get('description')
                        if isinstance(c.get('misprint'), dict)
                        else c.get('misprint'))
            psa = c.get('psa')
            notes = c.get('notes')
            pricing = c.get('pricing', {}) or {}
            low = pricing.get('low')
            mid = pricing.get('mid')
            high = pricing.get('high')
            effective_mid = pricing.get('effective_mid') or mid
            pricing_source = pricing.get('source')

            if coll_card and not is_replace:
                # MERGE: only fill empty-ish fields (keep your original merge behavior)
                if coll_card.card_set is None:
                    coll_card.card_set = card_set
                if not coll_card.condition:
                    coll_card.condition = condition
                if coll_card.quantity in (None, 0):
                    coll_card.quantity = quantity
                if not coll_card.misprint:
                    coll_card.misprint = misprint
                if not coll_card.psa:
                    coll_card.psa = psa
                if not coll_card.notes:
                    coll_card.notes = notes
                if coll_card.value_mid is None:
                    coll_card.value_low = low
                    coll_card.value_mid = mid
                    coll_card.value_high = high
                    coll_card.effective_mid = effective_mid
                    coll_card.pricing_source = pricing_source

                coll_card.import_batch = batch
                coll_card.save()
                updated += 1

            else:
                # REPLACE or new: ensure any existing single match is removed before creating
                if coll_card:
                    # delete previous images attached to that old row (should be rare in replace)
                    for img in coll_card.images.all():
                        try:
                            path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                            if os.path.exists(path):
                                os.remove(path)
                        except Exception:
                            pass
                        img.delete()
                    coll_card.delete()

                # Create new CollectionCard (fresh)
                coll_card = CollectionCard.objects.create(
                    card=card_obj,
                    card_set=card_set,
                    edition=edition,
                    condition=condition,
                    quantity=quantity,
                    misprint=misprint,
                    psa=psa,
                    notes=notes,
                    value_low=low,
                    value_mid=mid,
                    value_high=high,
                    effective_mid=effective_mid,
                    pricing_source=pricing_source,
                    import_batch=batch,
                    exported_id=exported_id
                )
                created += 1

            # --- Images (copy from tmpdir -> MEDIA_ROOT and attach to the freshly-created coll_card) ---
            img_data = c.get('images', {}) or {}
            img_path_in_json = img_data.get('img') if isinstance(img_data, dict) else None
            if img_path_in_json:
                img_filename = os.path.basename(img_path_in_json)

                # find file in extracted zip tmpdir
                found = None
                for root, _, files in os.walk(tmpdir):
                    if img_filename in files:
                        found = os.path.join(root, img_filename)
                        break

                if found:
                    dst = os.path.join(settings.MEDIA_ROOT, img_filename)
                    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
                    # copy/overwrite into MEDIA_ROOT
                    with open(found, 'rb') as fsrc, open(dst, 'wb') as fdst:
                        fdst.write(fsrc.read())

                    # attach to the current CollectionCard: store the filename (relative to MEDIA_ROOT)
                    CollectionImage.objects.create(
                        collection_card=coll_card,
                        img=img_filename
                    )

        # --- If you still want to delete any remaining old rows not present in JSON when using replace, you can do it,
        #     but we've already nuked the table at the start of replace so there's nothing left to delete here. ---
        # (keep for parity with previous logic if you switch to less-aggressive replace later)

        return created, updated, deleted




@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ('name','konami_id')

@admin.register(CardSet)
class CardSetAdmin(admin.ModelAdmin):
    list_display = ('name','code','release_date')

@admin.register(CollectionCard)
class CollectionCardAdmin(admin.ModelAdmin):
    list_display = ('card','card_set','edition','quantity','value_mid','import_batch')
    list_filter = ('import_batch',)
    search_fields = ('card__name','card_set__name','card_set__code')

@admin.register(CollectionImage)
class CollectionImageAdmin(admin.ModelAdmin):
    list_display = ('collection_card','img')
