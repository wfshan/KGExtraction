import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { DeploymentUnitOutlined } from '@ant-design/icons';
import WorkbenchPage from './pages/WorkbenchPage';
import GraphPage from './pages/GraphPage';
import './index.css';

function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1677FF',
          borderRadius: 8,
          fontFamily: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans SC', sans-serif`,
        },
        algorithm: theme.defaultAlgorithm,
      }}
    >
      <BrowserRouter>
        <div className="app-layout">
          <header className="app-header">
            <div className="logo">
              <span className="logo-icon">
                <DeploymentUnitOutlined />
              </span>
              KGExtraction
            </div>
            <nav>
              <NavLink to="/" end>
                🛠️ 工作台
              </NavLink>
              <NavLink to="/graph">
                🔗 图谱可视化
              </NavLink>
            </nav>
          </header>
          <main className="app-content">
            <Routes>
              <Route path="/" element={<WorkbenchPage />} />
              <Route path="/graph" element={<GraphPage />} />
              <Route path="/graph/:projectId" element={<GraphPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </ConfigProvider>
  );
}

export default App;
