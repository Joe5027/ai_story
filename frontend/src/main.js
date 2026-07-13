import { createApp } from 'vue';
import '@/assets/css/main.css';

import App from './App.vue';
import router from './router';
import store from './store';
import message from '@/utils/message';
import confirm from '@/utils/confirm';

const app = createApp(App);

// 注册全局消息提示
app.config.globalProperties.$message = message;

// 全局confirm对话框
app.config.globalProperties.$confirm = (msg, title = '确认', options = {}) => confirm.open(msg, title, options);
app.config.globalProperties.$alert = (msg, title = '提示', options = {}) => confirm.alert(msg, title, options);

// 全局错误处理
app.config.errorHandler = (err, vm, info) => {
  console.error('Vue Error:', err, info);
};

app.use(router);
app.use(store);

const vm = app.mount('#app');

// 初始化主题
const savedTheme = store.getters['ui/theme'];
if (savedTheme) {
  vm.$el.setAttribute('data-theme', savedTheme);
}
