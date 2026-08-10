import unittest
from unittest.mock import MagicMock

from app.services.sharefile_queue import ShareFileWorkQueue


class ShareFileQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_uses_short_visibility_and_requests_age_attributes(self):
        queue = ShareFileWorkQueue.__new__(ShareFileWorkQueue)
        queue.queue_url = "https://sqs.example.test/sharefile"
        queue.client = MagicMock()
        queue.client.receive_message.return_value = {"Messages": []}

        await queue.receive()

        queue.client.receive_message.assert_called_once_with(
            QueueUrl=queue.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=900,
            AttributeNames=["SentTimestamp", "ApproximateReceiveCount"],
        )


if __name__ == "__main__":
    unittest.main()
