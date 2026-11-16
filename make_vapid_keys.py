"""Print a new VAPID key pair for push notifications (daily-challenge reminders, see reminders.py).

    python make_vapid_keys.py

Put both lines in Vercel's environment variables (Production). Make them once and keep them: a new pair stops every
existing subscription from receiving reminders until players switch them on again.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def make_keys():
    """(public, private): the public key as the browser wants it (uncompressed point), the private key as raw bytes,
    both base64url without padding, the form pywebpush reads."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, 'big')
    return b64url(public), b64url(private)


if __name__ == '__main__':
    public, private = make_keys()
    print(f'VAPID_PUBLIC_KEY={public}')
    print(f'VAPID_PRIVATE_KEY={private}')
