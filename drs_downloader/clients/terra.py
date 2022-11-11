import subprocess
import tempfile
from pathlib import Path

import aiofiles
import aiohttp

from drs_downloader.models import DrsClient, DrsObject, AccessMethod, Checksum


class TerraDrsClient(DrsClient):
    """
    Calls the terra DRS server.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.endpoint = "https://us-central1-broad-dsde-prod.cloudfunctions.net/martha_v3"
        self.token = self._get_auth_token()

    @staticmethod
    def _get_auth_token() -> str:
        """Get Google Cloud authentication token.
        User must run 'gcloud auth login' from the shell before starting this script.

        Returns:
            str: auth token
        """
        token_command = "gcloud auth print-access-token"
        cmd = token_command.split(' ')
        token = subprocess.check_output(cmd).decode("ascii")[0:-1]
        assert token, "No token retrieved."
        return token

    async def download_part(self, drs_object: DrsObject, start: int, size: int, destination_path: Path) -> Path:

        headers = {'Range': f'bytes={start}-{size}'}
        (fd, name,) = tempfile.mkstemp(prefix=f'{drs_object.name}.{start}.{size}.', suffix='.part',
                                       dir=str(destination_path))
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(drs_object.access_methods[0].access_url) as request:
                file = await aiofiles.open(name, 'wb')
                self.statistics.set_max_files_open()
                await file.write(await request.content.read())
                return Path(name)

    async def sign_url(self, drs_object: DrsObject) -> DrsObject:
        """No-op.  terra returns a signed url in `get_object` """
        return drs_object

    async def get_object(self, object_id: str) -> DrsObject:
        """Sends a POST request for the signed URL, hash, and file size of a given DRS object.

        Args:
            object_id (str): DRS URI

        Raises:
            Exception: The request was rejected by the server

        Returns:
            DownloadURL: The downloadable bundle ready for async download
        """
        data = {
            "url": object_id,
            "fields": ["fileName", "size", "hashes", "accessUrl"]
        }
        session = aiohttp.ClientSession(headers={
            'authorization': 'Bearer ' + self.token,
            'content-type': 'application/json'
        })

        async with session.post(url=self.endpoint, json=data) as response:
            try:
                self.statistics.set_max_files_open()
                response.raise_for_status()
                resp = await response.json(content_type=None)
                assert 'accessUrl' in resp, resp
                if resp['accessUrl'] is None:
                    account_command = 'gcloud config get-value account'
                    cmd = account_command.split(' ')
                    account = subprocess.check_output(cmd).decode("ascii")
                    raise Exception(
                        f"A valid URL was not returned from the server.  Please check the access for {account}\n{resp}")
                url_ = resp['accessUrl']['url']
                md5_ = resp['hashes']['md5']
                size_ = resp['size']
                name_ = resp['fileName']
                return DrsObject(
                    self_uri=object_id,
                    size=size_,
                    checksums=[Checksum(checksum=md5_, type='md5')],
                    id=object_id,
                    name=name_,
                    access_methods=[AccessMethod(access_url=url_, type='gs')]
                )
            finally:
                await session.close()
