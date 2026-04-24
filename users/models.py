from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    USER_ROLES = [
        ('user', 'Regular User'),
        ('admin', 'Administrator'),
        ('manager', 'Manager'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=USER_ROLES, default='user')
    phone = models.CharField(max_length=15, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['role']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.role}"

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_manager(self):
        return self.role in ['admin', 'manager']


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
