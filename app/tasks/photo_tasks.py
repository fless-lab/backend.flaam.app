from __future__ import annotations

"""
Celery tasks — pipeline de modération photo (§16.1b).

Stubs : le worker Celery n'est pas encore câblé (sera fait en S11).
Les fonctions exposent une interface `.delay(photo_id)` compatible
avec Celery pour que le dispatcher (photo_moderation_service) puisse
les invoquer sans modification quand Celery sera prêt.

En attendant, `.delay()` log simplement l'enqueue et ne fait rien.
Les modes `onnx` et `external` ne sont donc pas encore fonctionnels
en production : le MVP reste en mode `manual`.
"""

import structlog

log = structlog.get_logger()


class _StubTask:
    """
    Stub compatible avec l'interface Celery Task.
    Quand Celery sera câblé (S11), remplacer par `@celery_app.task`.
    """

    def __init__(self, name: str):
        self._name = name

    def delay(self, photo_id: str) -> None:
        log.info("photo_task_enqueued_stub", task=self._name, photo_id=photo_id)


# Task ONNX : charge NSFW detector + face detector, score la photo,
# met à jour photo.moderation_status (approved / manual_review / rejected).
moderate_photo_onnx = _StubTask("moderate_photo_onnx")

# Task external : appelle Sightengine ou Google Vision, même logique.
moderate_photo_external = _StubTask("moderate_photo_external")


__all__ = ["moderate_photo_onnx", "moderate_photo_external"]
