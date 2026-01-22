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

        for c in data['cards']:
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
            coll_card, created_flag = CollectionCard.objects.get_or_create(
                exported_id=c.get('id'),
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
                coll_card.condition = c.get('condition')
                coll_card.quantity = c.get('quantity', coll_card.quantity)
                coll_card.misprint = (
                    c.get('misprint', {}).get('description')
                    if isinstance(c.get('misprint'), dict)
                    else coll_card.misprint
                )
                coll_card.notes = c.get('notes')
                coll_card.value_low = c.get('pricing', {}).get('low')
                coll_card.value_mid = c.get('pricing', {}).get('mid')
                coll_card.value_high = c.get('pricing', {}).get('high')
                coll_card.effective_mid = c.get('pricing', {}).get('effective_mid')
                coll_card.pricing_source = c.get('pricing', {}).get('source')
                coll_card.save()

            if created_flag:
                created += 1
            else:
                updated += 1

            # --- Images ---
            img_data = c.get('images', {})
            if img_data:
                img_path = img_data.get('img')
                if img_path:
                    img_filename = os.path.basename(img_path)
                    img_file_path = None

                    # Search for the image in the extracted folder
                    for root, dirs, files in os.walk(tmpdir):
                        if img_filename in files:
                            img_file_path = os.path.join(root, img_filename)
                            break

                    if img_file_path and os.path.exists(img_file_path):
                        # Save image file
                        upload_path = os.path.join(settings.MEDIA_ROOT, 'uploads', img_filename)
                        os.makedirs(os.path.dirname(upload_path), exist_ok=True)

                        with open(img_file_path, 'rb') as f:
                            with open(upload_path, 'wb') as dest:
                                dest.write(f.read())

                        # Save DB record only if not already linked
                        db_img_path = os.path.join('uploads', img_filename)

                        if not coll_card.images.filter(img=db_img_path).exists():
                            CollectionImage.objects.create(
                                collection_card=coll_card,
                                img=db_img_path
                            )

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
