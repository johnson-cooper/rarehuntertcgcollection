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
        """
        FULL TABLE REPLACE implementation:
        - Delete all CollectionImage files + rows
        - Delete all CollectionCard rows
        - Recreate CollectionCard rows from JSON
        - Save images found in the ZIP into Django storage and link them to created rows
        """
        created = 0
        updated = 0  # not used for replace, kept for API parity
        deleted = 0

        cards = data.get('cards') or []

        # 1) Delete existing images from disk and DB
        # iterate over all images to be sure we remove files
        all_images = CollectionImage.objects.all()
        for img in all_images:
            try:
                file_path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                # continue even if file removal fails
                pass
            # delete the model instance
            img.delete()

        # 2) Delete all collection cards
        deleted = CollectionCard.objects.count()
        CollectionCard.objects.all().delete()

        # 3) Recreate from JSON
        for c in cards:
            # Card
            konami = c.get('konami_id')
            name = c.get('name') or ''
            card_obj = None
            if konami:
                card_obj = Card.objects.filter(konami_id=konami).first()
            if not card_obj:
                card_obj = Card.objects.filter(name__iexact=name).first()
            if not card_obj:
                card_obj = Card.objects.create(name=name, konami_id=konami)

            # CardSet
            set_data = c.get('set') or {}
            card_set = None
            if set_data:
                code = set_data.get('code')
                set_name = set_data.get('name')
                if code:
                    card_set = CardSet.objects.filter(code__iexact=code).first()
                if not card_set and set_name:
                    card_set = CardSet.objects.filter(name__iexact=set_name).first()
                if not card_set:
                    # parse release_date if present
                    release_date = set_data.get('release_date', None)
                    card_set = CardSet.objects.create(
                        name=set_name or '',
                        code=code or None,
                        release_date=release_date or None
                    )

            # CollectionCard fields
            edition = c.get('edition', 'Unlimited')
            condition = c.get('condition') or ''
            quantity = c.get('quantity') or 1
            misprint_val = None
            misprint = c.get('misprint')
            if isinstance(misprint, dict):
                misprint_val = misprint.get('description') or None
            else:
                misprint_val = misprint or None
            psa = c.get('psa') or None
            notes = c.get('notes') or None

            pricing = c.get('pricing') or {}
            low = pricing.get('low')
            mid = pricing.get('mid')
            high = pricing.get('high')
            effective_mid = pricing.get('effective_mid')
            pricing_source = pricing.get('source')

            exported_id = c.get('id')

            # create collection card
            cc = CollectionCard.objects.create(
                card=card_obj,
                card_set=card_set,
                edition=edition,
                condition=condition,
                quantity=quantity,
                misprint=misprint_val,
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

            # Images: try to find matching file inside tmpdir (by basename)
            img_data = c.get('images') or {}
            img_path = img_data.get('img') if isinstance(img_data, dict) else None
            if img_path:
                filename = os.path.basename(img_path)
                found = None
                for root, _, files in os.walk(tmpdir):
                    if filename in files:
                        found = os.path.join(root, filename)
                        break
                if found:
                    # save via Django storage so upload_to settings are honored
                    try:
                        with open(found, 'rb') as f:
                            django_file = File(f, name=filename)
                            CollectionImage.objects.create(collection_card=cc, img=django_file)
                    except Exception as e:
                        # continue on image save errors
                        pass

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
