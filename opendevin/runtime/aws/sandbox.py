import asyncio
import os

import boto3
import websockets

from opendevin.core.config import config
from opendevin.core.logger import opendevin_logger as logger
from opendevin.core.schema import CancellableStream
from opendevin.runtime.aws.ssm_protocol_handler import SSMProtocolHandler
from opendevin.runtime.sandbox import Sandbox

AWS_ACCESS_KEY_ID = config.llm.aws_access_key_id
AWS_SECRET_ACCESS_KEY = config.llm.aws_secret_access_key
AWS_REGION_NAME = config.llm.aws_region_name
AWS_SESSION_TOKEN = config.llm.aws_session_token

# It needs to be set as an environment variable, if the variable is configured in the Config file.
if AWS_ACCESS_KEY_ID is None:
    AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
if AWS_SECRET_ACCESS_KEY is None:
    AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
if AWS_REGION_NAME is None:
    AWS_REGION_NAME = os.environ['AWS_REGION_NAME']
if AWS_SESSION_TOKEN is None:
    AWS_SESSION_TOKEN = os.environ['AWS_SESSION_TOKEN']


class AWSBox(Sandbox):
    closed = False
    _cwd: str = '/home/user'

    def __init__(
        self,
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
        self.timeout = timeout

        session_target_id = config.sandbox.aws_sandbox_target_id
        self.command_queue: asyncio.Queue = asyncio.Queue()

        self.ssm_client = boto3.client(
            service_name='ssm',
            region_name=AWS_REGION_NAME,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            aws_session_token=AWS_SESSION_TOKEN,
        )

        self.session = self.ssm_client.start_session(Target=session_target_id)
        logger.info(f'connected to {session_target_id}')

        asyncio.ensure_future(main_session_handler(self.session, self.command_queue))

        super().__init__()

    def execute(
        self, cmd: str, stream: bool = False, timeout: int | None = None
    ) -> tuple[int, str | CancellableStream]:
        logger.info(f'Executing command {cmd}')
        self.command_queue.put_nowait(cmd)
        logger.info(f'command queue size {self.command_queue.qsize()}')
        result = ''
        # logger.info(f'Received result {result}')
        return 0, result

    def close(self):
        logger.info('terminating session...')
        self.ssm_client.terminate_session(SessionId=self.session['SessionId'])

    def copy_to(self, host_src: str, sandbox_dest: str, recursive: bool = False):
        pass

    def get_working_directory(self):
        pass


async def main_session_handler(session, cmd_queue):
    uri = session['StreamUrl']
    logger.debug('wss: connecting to %s' % uri)
    async with websockets.connect(uri=uri, ping_interval=None) as websocket:
        ssm_handler = SSMProtocolHandler(
            websocket, session['SessionId'], session['TokenValue'], session['StreamUrl']
        )

        async def consumer_handler(socket, ssm):
            logger.info('consumer started.')
            async for message in socket:
                logger.info('consumer entered loop.')
                msg = ssm.deserialize_message(message)

                logger.info(
                    'received: %s' % msg['payload'].decode().replace('\n', '\r\n')
                )
                logger.info(msg['payload_type'])
                if msg['message_type'] in ['output_stream_data']:
                    await ssm.send_ack(msg)
                if msg['payload_type'] == 1:
                    # sys.stdout.write(msg['payload'].decode().replace('\n', '\r\n'))
                    # sys.stdout.flush()
                    logger.info(
                        'got text payload: %s'
                        % msg['payload'].decode().replace('\n', '\r\n')
                    )
                elif msg['payload_type'] == 17:
                    logger.debug('need to resend init')
                    await ssm.send_init(120, 80)

        async def producer_handler(socket, ssm):
            logger.info('producer started.')
            # time.sleep(10)
            try:
                await ssm.send(ssm.generate_token_message())
                await ssm.send_init(120, 80)
            except Exception:
                logger.info('received error')

            logger.info('producer entering loop.')
            while True:
                logger.info('producer entered loop.')
                await asyncio.sleep(0.05)
                try:
                    cmd = await cmd_queue.get()
                    logger.info('received command %s' % cmd)
                    await ssm.send_text(cmd + '\n\r')  # need newline to trigger command
                except Exception:
                    await ssm.send_text('\x04')
                    logger.info('received error')
                    break

        consumer_task = asyncio.ensure_future(consumer_handler(websocket, ssm_handler))
        producer_task = asyncio.ensure_future(producer_handler(websocket, ssm_handler))
        done, pending = await asyncio.wait(
            [producer_task, consumer_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for task in pending:
            task.cancel()
