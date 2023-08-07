"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
import redis
import json

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.dispatch import receiver
from django.db.models.signals import post_save

from tasks.models import Annotation
from io_storages.base_models import (
    ExportStorage,
    ExportStorageLink,
    ImportStorage,
    ImportStorageLink,
    ProjectStorageMixin
)


logger = logging.getLogger(__name__)


class RedisStorageMixin(models.Model):
    path = models.TextField(
        _('path'), null=True, blank=True,
        help_text='Storage prefix (optional)')
    host = models.TextField(
        _('host'), null=True, blank=True,
        help_text='Server Host IP (optional)')
    port = models.TextField(
        _('port'), null=True, blank=True,
        help_text='Server Port (optional)')
    password = models.TextField(
        _('password'), null=True, blank=True,
        help_text='Server Password (optional)')
    regex_filter = models.TextField(
        _('port'), null=True, blank=True,
        help_text='Cloud storage regex for filtering objects')
    use_blob_urls = models.BooleanField(
        _('use_blob_urls'), default=False,
        help_text='Interpret objects as BLOBs and generate URLs')

    def get_redis_connection(self, db=None, redis_config={}):  # type: ignore[no-untyped-def]
        """Get a redis connection from the provided arguments.

        Args:
            db (int): Database ID of database to use. This needs to
                      always be provided to prevent accidental overwrite
                      to a default value. Therefore, the default is None,
                      but raises an error if not provided.
            redis_config (dict, optional): Further redis configuration.

        Returns:
            redis.StrictRedis object with connection to database.
        """
        if not db:
            # This should never happen, but better to check than to accidentally
            # overwrite an existing database by choosing a wrong default:
            raise ValueError(
                "Please explicitly pass a redis db id to prevent accidentally overwriting existing database!")

        # Since tasks are always text, we use StrictRedis with utf-8 decoding.
        r = redis.StrictRedis(db=db, charset="utf-8", decode_responses=True, **redis_config)
        # Test connection
        # (this will raise redis.exceptions.ConnectionError if it cannot connect)
        r.ping()
        return r

    def get_client(self):  # type: ignore[no-untyped-def]
        redis_config = {}
        if self.host: redis_config["host"] = self.host
        if self.port: redis_config["port"] = self.port
        if self.password: redis_config["password"] = self.password

        return self.get_redis_connection(db=self.db, redis_config=redis_config)  # type: ignore[attr-defined, no-untyped-call]


class RedisImportStorageBase(ImportStorage, RedisStorageMixin):
    db = models.PositiveSmallIntegerField(
        _('db'), default=1,
        help_text='Server Database')

    def can_resolve_url(self, url):  # type: ignore[no-untyped-def]
        return False

    def iterkeys(self):  # type: ignore[no-untyped-def]
        client = self.get_client()  # type: ignore[no-untyped-call]
        path = str(self.path)
        for key in client.keys(path + '*'):
            yield key

    def get_data(self, key):  # type: ignore[no-untyped-def]
        client = self.get_client()  # type: ignore[no-untyped-call]
        value = client.get(key)
        if not value:
            return
        return json.loads(value)

    def scan_and_create_links(self):  # type: ignore[no-untyped-def]
        return self._scan_and_create_links(RedisImportStorageLink)  # type: ignore[no-untyped-call]

    def validate_connection(self, client=None):  # type: ignore[no-untyped-def]
        if client is None:
            client = self.get_client()  # type: ignore[no-untyped-call]
        client.ping()

    class Meta:
        abstract = True


class RedisImportStorage(ProjectStorageMixin, RedisImportStorageBase):
    class Meta:
        abstract = False


class RedisExportStorage(RedisStorageMixin, ExportStorage):
    db = models.PositiveSmallIntegerField(
        _('db'), default=2,
        help_text='Server Database')

    def save_annotation(self, annotation):  # type: ignore[no-untyped-def]
        client = self.get_client()  # type: ignore[no-untyped-call]
        logger.debug(f'Creating new object on {self.__class__.__name__} Storage {self} for annotation {annotation}')
        ser_annotation = self._get_serialized_data(annotation)  # type: ignore[no-untyped-call]

        # get key that identifies this object in storage
        key = RedisExportStorageLink.get_key(annotation)  # type: ignore[no-untyped-call]

        # put object into storage
        client.set(key, json.dumps(ser_annotation))

        # create link if everything ok
        RedisExportStorageLink.create(annotation, self)  # type: ignore[no-untyped-call]


@receiver(post_save, sender=Annotation)
def export_annotation_to_redis_storages(sender, instance, **kwargs):  # type: ignore[no-untyped-def]
    project = instance.project
    if hasattr(project, 'io_storages_redisexportstorages'):
        for storage in project.io_storages_redisexportstorages.all():
            logger.debug(f'Export {instance} to Redis storage {storage}')
            storage.save_annotation(instance)


class RedisImportStorageLink(ImportStorageLink):
    storage = models.ForeignKey(RedisImportStorage, on_delete=models.CASCADE, related_name='links')


class RedisExportStorageLink(ExportStorageLink):
    storage = models.ForeignKey(RedisExportStorage, on_delete=models.CASCADE, related_name='links')
