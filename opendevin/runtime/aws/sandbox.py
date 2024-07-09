from opendevin.core.config import config
from opendevin.core.schema import CancellableStream
from opendevin.runtime.sandbox import Sandbox


class AWSBox(Sandbox):
    closed = False
    _cwd: str = '/home/user'

    def __init__(
        self,
        project: str,
        timeout: int = config.sandbox.timeout,
    ):
        """
        Create a new AWS sandbox, using configuration defined in CodeBuild project identified by `project`.

        1. start a build with debug mode. Waiting for the build to start a SSM session and pause.
        2. call start-session API to get a session url.
        3. create a websocket using the returned session

            session = client.start_session(
                Target='session-id-returned-by-CodeBuild'
            )

            with websockets.connect(
                  uri=session['StreamUrl'],
                  ping_interval=None
            ) as websocket:
              ...

        SSM-protocol-handler reference implementation: https://github.com/richinfante/aws-ssh/blob/main/main.py#L254-L257
        """
        self.project = AWSBox(project)
        self.timeout = timeout
        super().__init__()

    def execute(
        self, cmd: str, stream: bool = False, timeout: int | None = None
    ) -> tuple[int, str | CancellableStream]:
        return 200, ''

    def close(self):
        pass

    def copy_to(self, host_src: str, sandbox_dest: str, recursive: bool = False):
        pass

    def get_working_directory(self):
        pass
