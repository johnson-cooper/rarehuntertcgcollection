from django.contrib import admin, messages
from django import forms
import json, zipfile, os, tempfile
from django.db import transaction
from django.core.files import File
from django.conf import settings
from .models import Card, CardSet, CollectionCard, ImportBatch, CollectionImage

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
        created = updated = deleted = 0
        incoming_ids = set()
        is_replace = batch.mode == 'replace'

        # --- REPLACE MODE: delete ALL previous cards + images for this batch first ---
        if is_replace:
            old_cards = CollectionCard.objects.filter(import_batch=batch)
            for cc in old_cards:
                for img in cc.images.all():
                    path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                    if os.path.exists(path):
                        os.remove(path)
                    img.delete()
                cc.delete()
            deleted = old_cards.count()

        for c in data['cards']:
            exported_id = c.get('id')
            incoming_ids.add(exported_id)

            # --- Card ---
            card_obj, _ = Card.objects.get_or_create(
                konami_id=c.get('konami_id'),
                defaults={'name': c['name']}
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
            coll_card = CollectionCard.objects.filter(
                exported_id=exported_id,
                import_batch=batch
            ).first()

            edition = c.get('edition', 'Unlimited')
            condition = c.get('condition')
            quantity = c.get('quantity', 1)
            misprint = (c.get('misprint', {}).get('description')
                        if isinstance(c.get('misprint'), dict)
                        else None)
            psa = c.get('psa')
            notes = c.get('notes')
            pricing = c.get('pricing', {})
            low = pricing.get('low')
            mid = pricing.get('mid')
            high = pricing.get('high')
            effective_mid = pricing.get('effective_mid') or mid
            pricing_source = pricing.get('source')

            if coll_card:
                # update existing card
                coll_card.card = card_obj
                coll_card.card_set = card_set
                coll_card.edition = edition
                coll_card.condition = condition
                coll_card.quantity = quantity
                coll_card.misprint = misprint
                coll_card.psa = psa
                coll_card.notes = notes
                coll_card.value_low = low
                coll_card.value_mid = mid
                coll_card.value_high = high
                coll_card.effective_mid = effective_mid
                coll_card.pricing_source = pricing_source
                coll_card.import_batch = batch
                coll_card.save()
                updated += 1
            else:
                # create new
                CollectionCard.objects.create(
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

            # --- Images ---
            img_data = c.get('images', {})
            if img_data and img_data.get('img'):
                img_filename = os.path.basename(img_data['img'])

                # Delete existing images if replace
                if is_replace and coll_card:
                    for old_img in coll_card.images.all():
                        path = os.path.join(settings.MEDIA_ROOT, str(old_img.img))
                        if os.path.exists(path):
                            os.remove(path)
                        old_img.delete()

                # Add image
                for root, _, files in os.walk(tmpdir):
                    if img_filename in files:
                        src = os.path.join(root, img_filename)
                        dst = os.path.join(settings.MEDIA_ROOT, img_filename)
                        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
                        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                            fdst.write(fsrc.read())

                        CollectionImage.objects.create(
                            collection_card=coll_card or CollectionCard.objects.filter(
                                exported_id=exported_id, import_batch=batch
                            ).first(),
                            img=img_filename
                        )
                        break

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
