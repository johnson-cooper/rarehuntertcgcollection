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

        for c in data['cards']:
            exported_id = c.get('id')
            incoming_ids.add(exported_id)

            card_obj, _ = Card.objects.get_or_create(
                konami_id=c.get('konami_id'),
                defaults={'name': c['name']}
            )

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

            coll_card, created_flag = CollectionCard.objects.get_or_create(
                exported_id=exported_id,
                import_batch=batch,
                defaults={
                    'card': card_obj,
                    'card_set': card_set,
                    'edition': c.get('edition', 'Unlimited'),
                    'condition': c.get('condition'),
                    'quantity': c.get('quantity', 1),
                    'misprint': (
                        c.get('misprint', {}).get('description')
                        if isinstance(c.get('misprint'), dict)
                        else None
                    ),
                    'psa': c.get('psa'),
                    'notes': c.get('notes'),
                    'value_low': c.get('pricing', {}).get('low'),
                    'value_mid': c.get('pricing', {}).get('mid'),
                    'value_high': c.get('pricing', {}).get('high'),
                    'effective_mid': c.get('pricing', {}).get('effective_mid'),
                    'pricing_source': c.get('pricing', {}).get('source'),
                }
            )

            if not created_flag:
                if is_replace:
                    # FULL overwrite
                    coll_card.card = card_obj
                    coll_card.card_set = card_set
                    coll_card.edition = c.get('edition', coll_card.edition)
                    coll_card.condition = c.get('condition')
                    coll_card.quantity = c.get('quantity', coll_card.quantity)
                    coll_card.misprint = (
                        c.get('misprint', {}).get('description')
                        if isinstance(c.get('misprint'), dict)
                        else None
                    )
                    coll_card.notes = c.get('notes')
                    coll_card.value_low = c.get('pricing', {}).get('low')
                    coll_card.value_mid = c.get('pricing', {}).get('mid')
                    coll_card.value_high = c.get('pricing', {}).get('high')
                    coll_card.effective_mid = c.get('pricing', {}).get('effective_mid')
                    coll_card.pricing_source = c.get('pricing', {}).get('source')

                else:
                    # MERGE: only fill missing fields
                    if coll_card.card_set is None:
                        coll_card.card_set = card_set
                    if coll_card.condition is None:
                        coll_card.condition = c.get('condition')
                    if coll_card.quantity in (None, 0):
                        coll_card.quantity = c.get('quantity', coll_card.quantity)
                    if coll_card.misprint is None:
                        coll_card.misprint = (
                            c.get('misprint', {}).get('description')
                            if isinstance(c.get('misprint'), dict)
                            else None
                        )
                    if coll_card.notes is None:
                        coll_card.notes = c.get('notes')
                    if coll_card.value_mid is None:
                        coll_card.value_mid = c.get('pricing', {}).get('mid')

                coll_card.save()
                updated += 1
            else:
                created += 1

            # --- Images ---
            img_data = c.get('images', {})
            if img_data and img_data.get('img'):
                img_filename = os.path.basename(img_data['img'])

                # MERGE: skip if image already exists
                if not is_replace and coll_card.images.exists():
                    continue

                # REPLACE: delete old images
                if is_replace:
                    for old_img in coll_card.images.all():
                        old_path = os.path.join(settings.MEDIA_ROOT, str(old_img.img))
                        if os.path.exists(old_path):
                            os.remove(old_path)
                        old_img.delete()

                for root, _, files in os.walk(tmpdir):
                    if img_filename in files:
                        src = os.path.join(root, img_filename)
                        dst = os.path.join(settings.MEDIA_ROOT, img_filename)
                        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
                        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                            fdst.write(fsrc.read())

                        CollectionImage.objects.create(
                            collection_card=coll_card,
                            img=img_filename
                        )
                        break

        # --- DELETE missing cards ONLY in replace ---
        if is_replace:
            to_delete = CollectionCard.objects.filter(
                import_batch=batch
            ).exclude(exported_id__in=incoming_ids)

            for card in to_delete:
                for img in card.images.all():
                    path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                    if os.path.exists(path):
                        os.remove(path)
                    img.delete()
                card.delete()
                deleted += 1

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
