from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.inference.models import RuntimeNode


User = get_user_model()


class RuntimeNodeSecurityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('runtime-user', password='secret123')
        self.staff = User.objects.create_user(
            'runtime-admin', password='secret123', is_staff=True
        )
        self.node = RuntimeNode.objects.create(
            name='permission-node',
            node_type='local_gpu',
            agent_url='http://127.0.0.1:19100',
            access_token='runtime-secret',
            is_local=True,
        )

    @patch('apps.inference.views.requests.post')
    @patch('apps.inference.views.requests.get')
    def test_regular_user_can_read_but_cannot_write_health_or_reload(
        self, requests_get, requests_post
    ):
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.get('/api/v1/models/runtime-nodes/').status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(
                '/api/v1/models/runtime-nodes/',
                {
                    'name': 'forbidden-node',
                    'node_type': 'local_gpu',
                    'agent_url': 'http://127.0.0.1:19101',
                    'is_local': True,
                },
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(
                f'/api/v1/models/runtime-nodes/{self.node.id}/',
                {'name': 'forbidden-update'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(
                f'/api/v1/models/runtime-nodes/{self.node.id}/'
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f'/api/v1/models/runtime-nodes/{self.node.id}/refresh-health/'
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f'/api/v1/models/runtime-nodes/{self.node.id}/runtime-reload/',
                {},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        requests_get.assert_not_called()
        requests_post.assert_not_called()

    def test_runtime_node_rejects_non_loopback_http_userinfo_and_non_http_scheme(self):
        self.client.force_authenticate(self.staff)

        def create(agent_url, name):
            return self.client.post(
                '/api/v1/models/runtime-nodes/',
                {
                    'name': name,
                    'node_type': 'rented_gpu',
                    'agent_url': agent_url,
                    'access_token': 'runtime-secret',
                    'is_local': False,
                    'is_active': True,
                },
                format='json',
            )

        self.assertEqual(
            create('http://runtime.example.com:9100', 'remote-http').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            create('https://user:pass@runtime.example.com', 'remote-userinfo').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            create('ftp://runtime.example.com', 'remote-ftp').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            create('https://runtime.example.com', 'remote-https').status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            create('http://[::1]:9100', 'loopback-http').status_code,
            status.HTTP_201_CREATED,
        )
