import unittest
from unittest.mock import MagicMock

from app.services.sharefile_queue import ShareFileWorkQueue


class ShareFileQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_requests_a_burst_with_short_visibility_and_age_attributes(self):
        queue = ShareFileWorkQueue.__new__(ShareFileWorkQueue)
        queue.queue_url = "https://sqs.example.test/sharefile"
        queue.client = MagicMock()
        queue.client.receive_message.return_value = {"Messages": []}

        await queue.receive()

        queue.client.receive_message.assert_called_once_with(
            QueueUrl=queue.queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
            VisibilityTimeout=900,
            AttributeNames=["SentTimestamp", "ApproximateReceiveCount"],
        )

    async def test_receive_clamps_sqs_batch_and_wait_limits(self):
        queue = ShareFileWorkQueue.__new__(ShareFileWorkQueue)
        queue.queue_url = "https://sqs.example.test/sharefile"
        queue.client = MagicMock()
        queue.client.receive_message.return_value = {"Messages": []}

        await queue.receive(max_messages=99, wait_time_seconds=-1)

        queue.client.receive_message.assert_called_once_with(
            QueueUrl=queue.queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
            VisibilityTimeout=900,
            AttributeNames=["SentTimestamp", "ApproximateReceiveCount"],
        )


if __name__ == "__main__":
    unittest.main()
