import apiClient from '@/services/apiClient';

const optionalAgentRequest = {
  suppressGlobalError: true,
};


const buildStreamUrl = (scopeKey, streamToken, accessToken) => {
  const baseUrl = (process.env.VUE_APP_API_BASE_URL || '/api/v1').replace(/\/$/, '');
  const query = new URLSearchParams({ stream_token: streamToken });
  if (accessToken) {
    query.set('access_token', accessToken);
  }
  return `${baseUrl}/agent/session/${encodeURIComponent(scopeKey)}/stream/?${query.toString()}`;
};


export default {
  initSession(data) {
    return apiClient.post('/agent/session/init/', data, optionalAgentRequest);
  },

  getModels() {
    return apiClient.get('/agent/models/', optionalAgentRequest);
  },

  updateSelectedModel(data) {
    return apiClient.post('/agent/models/', data, optionalAgentRequest);
  },

  sendMessage(scopeKey, data) {
    return apiClient.post(`/agent/session/${encodeURIComponent(scopeKey)}/message/`, data, optionalAgentRequest);
  },

  sendUiResult(scopeKey, data) {
    return apiClient.post(`/agent/session/${encodeURIComponent(scopeKey)}/ui-result/`, data, optionalAgentRequest);
  },

  abort(scopeKey) {
    return apiClient.post(`/agent/session/${encodeURIComponent(scopeKey)}/abort/`, null, optionalAgentRequest);
  },

  clear(scopeKey) {
    return apiClient.post(`/agent/session/${encodeURIComponent(scopeKey)}/clear/`, null, optionalAgentRequest);
  },

  buildStreamUrl,
};
