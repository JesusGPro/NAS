from django.db import models

class HardDrive(models.Model):
    name = models.CharField(max_length=50)
    total_size_gb = models.IntegerField()
    used_size_gb = models.IntegerField()
    is_online = models.BooleanField(default=True)

    def __str__(self):
        return self.name
