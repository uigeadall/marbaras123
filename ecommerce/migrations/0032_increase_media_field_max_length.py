# Cloudinary public_id paths exceed default varchar(100); fixes admin upload 500s.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0031_add_earring_hoop_size_variant_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bannerimage",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="Banner image for carousel",
                max_length=512,
                null=True,
                upload_to="banners/",
            ),
        ),
        migrations.AlterField(
            model_name="bannerimage",
            name="video_file",
            field=models.FileField(
                blank=True,
                help_text="Upload a video file (MP4, WebM, OGG). Video will autoplay, loop, and be muted like a GIF. Max size: 500MB",
                max_length=512,
                null=True,
                upload_to="banners/videos/",
            ),
        ),
        migrations.AlterField(
            model_name="blogpost",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="Featured image for blog post",
                max_length=512,
                null=True,
                upload_to="blog/",
            ),
        ),
        migrations.AlterField(
            model_name="blogpost",
            name="video_file",
            field=models.FileField(
                blank=True,
                help_text="Upload a video file (MP4, WebM, OGG). Max size: 100MB",
                max_length=512,
                null=True,
                upload_to="blog/videos/",
            ),
        ),
        migrations.AlterField(
            model_name="category",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="Image for sub-category display",
                max_length=512,
                null=True,
                upload_to="categories/",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="image",
            field=models.ImageField(
                blank=True,
                max_length=512,
                null=True,
                upload_to="products/",
            ),
        ),
        migrations.AlterField(
            model_name="productimage",
            name="image",
            field=models.ImageField(
                max_length=512,
                upload_to="products/multiple/",
            ),
        ),
    ]
