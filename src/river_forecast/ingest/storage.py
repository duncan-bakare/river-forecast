import gzip


class MemoryStore:
    """In-memory store for tests and dry runs."""

    def __init__(self):
        self.objects = {}

    def put(self, key, body, content_type="application/octet-stream"):
        self.objects[key] = body

    def exists(self, key):
        return key in self.objects

    def get(self, key):
        return self.objects[key]


class S3Store:
    def __init__(self, bucket, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.bucket = bucket
        self.client = client

    def put(self, key, body, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def exists(self, key):
        resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
        return any(o["Key"] == key for o in resp.get("Contents", []))

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def gz(data):
    # mtime=0 keeps output deterministic for identical input.
    return gzip.compress(data, mtime=0)
