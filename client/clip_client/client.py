import mimetypes
import time
from typing import (
    overload,
    TYPE_CHECKING,
    Optional,
    Union,
    Iterator,
    Generator,
    Iterable,
    Dict,
)
from urllib.parse import urlparse

if TYPE_CHECKING:
    from docarray import DocumentArray, Document
    import numpy as np


class Client:
    def __init__(self, server: str):
        """Create a Clip client object that connects to the Clip server.

        Server scheme is in the format of `scheme://netloc:port`, where
            - scheme: one of grpc, websocket, http, grpcs, websockets, https
            - netloc: the server ip address or hostname
            - port: the public port of the server

        :param server: the server URI
        """
        try:
            r = urlparse(server)
            _port = r.port
            _scheme = r.scheme
            if not _port:
                raise
            if not _scheme:
                raise
        except:
            raise ValueError(f'{server} is not a valid scheme')

        _tls = False

        if _scheme in ('grpcs', 'https', 'wss'):
            _scheme = _scheme[:-1]
            _tls = True

        if _scheme == 'ws':
            _scheme = 'websocket'  # temp fix for the core

        if _scheme in ('grpc', 'http', 'ws', 'websocket'):
            _kwargs = dict(host=r.hostname, port=_port, protocol=_scheme, https=_tls)

            from jina import Client

            self._client = Client(**_kwargs)
            self._async_client = Client(**_kwargs, asyncio=True)

    @overload
    def encode(
        self,
        content: Iterable[str],
        *,
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> 'np.ndarray':
        """Encode images and texts into embeddings where the input is an iterable of raw strings.

        Each image and text must be represented as a string. The following strings are acceptable:

            - local image filepath, will be considered as an image
            - remote image http/https, will be considered as an image
            - a dataURI, will be considered as an image
            - plain text, will be considered as a sentence

        :param content: an iterator of image URIs or sentences, each element is an image or a text sentence as a string.
        :param batch_size: the number of elements in each request when sending ``content``
        :param show_progress: if set, show a progress bar
        :return: the embedding in a numpy ndarray with shape ``[N, D]``. ``N`` is in the same length of ``content``
        """
        ...

    @overload
    def encode(
        self,
        content: Union['DocumentArray', Iterable['Document']],
        *,
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> 'DocumentArray':
        """Encode images and texts into embeddings where the input is an iterable of :class:`docarray.Document`.

        :param content: an iterable of :class:`docarray.Document`, each Document must be filled with `.uri`, `.text` or `.blob`.
        :param batch_size: the number of elements in each request when sending ``content``
        :param show_progress: if set, show a progress bar
        :return: the embedding in a numpy ndarray with shape ``[N, D]``. ``N`` is in the same length of ``content``
        """
        ...

    def encode(self, content, **kwargs):
        if isinstance(content, str):
            raise TypeError(
                f'content must be an Iterable of [str, Document], try `.encode(["{content}"])` instead'
            )

        r = self._client.post(**self._get_post_payload(content, kwargs))
        return r.embeddings if self._return_plain else r

    def _iter_doc(self, content) -> Generator['Document', None, None]:
        from docarray import Document

        self._return_plain = True

        for c in content:
            if isinstance(c, str):
                self._return_plain = True
                _mime = mimetypes.guess_type(c)[0]
                if _mime and _mime.startswith('image'):
                    yield Document(uri=c).load_uri_to_blob()
                else:
                    yield Document(text=c)
            elif isinstance(c, Document):
                if c.content_type in ('text', 'blob'):
                    self._return_plain = False
                    yield c
                elif not c.blob and c.uri:
                    c.load_uri_to_blob()
                    self._return_plain = False
                    yield c
                else:
                    raise TypeError(f'unsupported input type {c!r} {c.content_type}')
            else:
                raise TypeError(f'unsupported input type {c!r}')

    def _get_post_payload(self, content, kwargs):
        return dict(
            on='/',
            inputs=self._iter_doc(content),
            show_progress=kwargs.get('show_progress'),
            request_size=kwargs.get('batch_size', 8),
            total_docs=len(content) if hasattr(content, '__len__') else None,
        )

    def profile(self, content: Optional[str] = '') -> Dict[str, float]:
        """Profiling a single query's roundtrip including network and compuation latency. Results is summarized in a table.

        :param content: the content to be sent for profiling. By default it sends an empty Document
            that helps you understand the network latency.
        :return: the latency report in a dict.
        """
        st = time.perf_counter()
        r = self._client.post('/', self._iter_doc([content]), return_responses=True)
        ed = (time.perf_counter() - st) * 1000
        route = r[0].routes
        gateway_time = (
            route[0].end_time.ToMilliseconds() - route[0].start_time.ToMilliseconds()
        )
        clip_time = (
            route[1].end_time.ToMilliseconds() - route[1].start_time.ToMilliseconds()
        )
        network_time = ed - gateway_time
        server_network = gateway_time - clip_time

        from rich.table import Table

        def make_table(_title, _time, _percent):
            table = Table(show_header=False, box=None)
            table.add_row(
                _title, f'[b]{_time:.0f}[/b]ms', f'[dim]{_percent * 100:.0f}%[/dim]'
            )
            return table

        from rich.tree import Tree

        t = Tree(make_table('Roundtrip', ed, 1))
        t.add(make_table('Client-server network', network_time, network_time / ed))
        t2 = t.add(make_table('Server', gateway_time, gateway_time / ed))
        t2.add(
            make_table(
                'Gateway-CLIP network', server_network, server_network / gateway_time
            )
        )
        t2.add(make_table('CLIP model', clip_time, clip_time / gateway_time))

        from rich import print

        print(t)

        return {
            'Roundtrip': ed,
            'Client-server network': network_time,
            'Server': gateway_time,
            'Gateway-CLIP network': server_network,
            'CLIP model': clip_time,
        }

    @overload
    async def aencode(
        self,
        content: Iterator[str],
        *,
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> 'np.ndarray':
        ...

    @overload
    async def aencode(
        self,
        content: Union['DocumentArray', Iterable['Document']],
        *,
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> 'DocumentArray':
        ...

    async def aencode(self, content, **kwargs):
        from docarray import DocumentArray

        r = DocumentArray()
        async for da in self._async_client.post(
            **self._get_post_payload(content, kwargs)
        ):
            r.extend(da)
        return r.embeddings if self._return_plain else r
