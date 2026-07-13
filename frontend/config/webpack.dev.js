const { merge } = require('webpack-merge');
process.env.NODE_ENV = process.env.NODE_ENV || 'development';
const common = require('./webpack.common.js');
const backendPort = process.env.BACKEND_PORT || '8010';
const backendTarget = process.env.BACKEND_TARGET || `http://127.0.0.1:${backendPort}`;

module.exports = merge(common, {
  mode: 'development',
  devtool: 'eval-source-map',
  devServer: {
    port: 3000,
    allowedHosts: ['localhost', '127.0.0.1'],
    hot: true,
    open: true,
    historyApiFallback: true,
    client: {
      overlay: {
        errors: true,
        warnings: false,
      },
    },
    proxy: [
      {
        context: ['/api'],
        target: backendTarget,
        changeOrigin: true,
        secure: false,
      },
      {
        context: ['/media'],
        target: backendTarget,
        changeOrigin: true,
        secure: false,
      },
    ],
  },
});
