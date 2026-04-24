from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0007_auto_20260324_2207"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="trailer_url",
            field=models.URLField(
                blank=True,
                null=True,
                help_text="Full YouTube URL (youtube.com or youtu.be). Stored safely as video ID.",
            ),
        ),
    ]
