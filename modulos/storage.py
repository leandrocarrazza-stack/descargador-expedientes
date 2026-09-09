"""
Storage de PDFs finales
========================

Los PDFs unificados vivían solo en output/ (disco efímero de Render) y se
borraban a los 10s de servirse la primera vez (ver el `_borrar_diferido`
que rutas/descargas.py ya no llama). Esto hace que el botón "Descargar PDF"
del historial, y el enlace que manda el aviso por email, sigan funcionando
mucho después de esa primera descarga.

`StoragePDF` es la interfaz que usa el resto de la app; hay dos backends:

- R2 (Cloudflare, S3-compatible vía boto3): producción, si
  config.STORAGE_HABILITADO es True.
- Local (disco, en config.PDF_STORE_DIR): desarrollo, o producción sin R2
  configurado — nunca rompe la app por falta de credenciales, solo pierde
  la persistencia de 180 días.

Subida y bajada son streaming (upload_file/download_file de boto3, o
shutil.copy en el backend local): nunca se carga el PDF completo en
memoria, crítico en un servidor con ~512 MB de RAM total.
"""

import logging
import shutil
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class StoragePDF:
    """Interfaz común. Instanciar vía storage_pdf() (singleton por app)."""

    def guardar(self, ruta_local: str, key: str) -> None:
        raise NotImplementedError

    def descargar(self, key: str, ruta_local: str) -> bool:
        """Retorna True si se descargó, False si la key no existe."""
        raise NotImplementedError

    def borrar(self, key: str) -> None:
        raise NotImplementedError

    def existe(self, key: str) -> bool:
        raise NotImplementedError

    def url_firmada(self, key: str, segundos: int = 3600):
        """Devuelve una URL firmada temporal, o None si el backend no la soporta."""
        return None


class StorageLocal(StoragePDF):
    """Backend de desarrollo: guarda los PDFs en config.PDF_STORE_DIR."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _ruta(self, key: str) -> Path:
        # Las keys usan '/' como separador lógico (usuarios/<id>/expedientes/...);
        # se preservan como subcarpetas reales dentro de PDF_STORE_DIR.
        ruta = self.base_dir / key
        ruta.parent.mkdir(parents=True, exist_ok=True)
        return ruta

    def guardar(self, ruta_local: str, key: str) -> None:
        shutil.copyfile(ruta_local, self._ruta(key))

    def descargar(self, key: str, ruta_local: str) -> bool:
        origen = self._ruta(key)
        if not origen.exists():
            return False
        Path(ruta_local).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, ruta_local)
        return True

    def borrar(self, key: str) -> None:
        try:
            self._ruta(key).unlink(missing_ok=True)
        except Exception:
            logger.warning(f"[STORAGE] No se pudo borrar {key} del backend local", exc_info=True)

    def existe(self, key: str) -> bool:
        return self._ruta(key).exists()


class StorageR2(StoragePDF):
    """Backend de producción: Cloudflare R2 (API S3-compatible) vía boto3."""

    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str):
        import boto3
        self.bucket = bucket
        self._cliente = boto3.client(
            's3',
            endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name='auto',
        )

    def guardar(self, ruta_local: str, key: str) -> None:
        self._cliente.upload_file(ruta_local, self.bucket, key)

    def descargar(self, key: str, ruta_local: str) -> bool:
        if not self.existe(key):
            return False
        Path(ruta_local).parent.mkdir(parents=True, exist_ok=True)
        self._cliente.download_file(self.bucket, key, ruta_local)
        return True

    def borrar(self, key: str) -> None:
        try:
            self._cliente.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            logger.warning(f"[STORAGE] No se pudo borrar {key} de R2", exc_info=True)

    def existe(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._cliente.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey'):
                return False
            raise

    def url_firmada(self, key: str, segundos: int = 3600):
        return self._cliente.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=segundos,
        )


_instancia = None


def storage_pdf() -> StoragePDF:
    """Devuelve el backend configurado (singleton de proceso)."""
    global _instancia
    if _instancia is None:
        if config.STORAGE_HABILITADO:
            _instancia = StorageR2(
                config.R2_ACCOUNT_ID,
                config.R2_ACCESS_KEY_ID,
                config.R2_SECRET_ACCESS_KEY,
                config.R2_BUCKET,
            )
            logger.info("[STORAGE] Backend R2 activo")
        else:
            _instancia = StorageLocal(config.PDF_STORE_DIR)
            if config.FLASK_ENV == 'production':
                # No es un simple "se pierde la persistencia": en producción
                # este directorio vive en el disco persistente de Render
                # (mismo disco que la sesión de Mesa Virtual, ver
                # MESA_VIRTUAL_SESSION_PATH) y limpiar_pdfs_antiguos() NO lo
                # toca — solo limpia output/. Sin R2, los PDFs se acumulan
                # ahí hasta RETENCION_PDF_DIAS sin límite de tamaño.
                logger.warning(
                    "[STORAGE] R2 no configurado en producción: los PDFs se acumulan "
                    f"sin límite de tamaño en {config.PDF_STORE_DIR} (disco persistente) "
                    f"hasta RETENCION_PDF_DIAS={config.RETENCION_PDF_DIAS} días. "
                    "Configurá R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET."
                )
            else:
                logger.info(f"[STORAGE] Backend local activo ({config.PDF_STORE_DIR})")
    return _instancia


def key_pdf_usuario(user_id: int, numero_expediente: str, token: str) -> str:
    """Genera la key de storage para el PDF de un (usuario, expediente, intento)."""
    numero_sanitizado = "".join(c if c.isalnum() else "_" for c in numero_expediente)
    return f"usuarios/{user_id}/expedientes/{numero_sanitizado}-{token}.pdf"
