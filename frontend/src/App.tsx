import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import GraphPage from './pages/GraphPage';
import { IngestPage, OntologyPage, GovernPage } from './pages/workspaces';
import AppRail from './components/AppRail';
import { ProjectProvider } from './store/project';
import './index.css';

/** 图谱页要全幅画布，主区在该路由下取消内边距 */
function MainArea() {
  const { pathname } = useLocation();
  const fullBleed = pathname.startsWith('/graph');
  return (
    <main className={`app-main${fullBleed ? ' full-bleed' : ''}`}>
      <Routes>
        <Route path="/" element={<Navigate to="/ingest" replace />} />
        <Route path="/ingest" element={<IngestPage />} />
        <Route path="/ontology" element={<OntologyPage />} />
        <Route path="/govern" element={<GovernPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/graph/:projectId" element={<GraphPage />} />
      </Routes>
    </main>
  );
}

function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        // 暗色专业工作台：darkAlgorithm 负责组件层换肤，token 负责品牌与语义层
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#7B93FF',
          colorInfo: '#7B93FF',
          colorSuccess: '#3DDC97',
          colorWarning: '#F0B429',
          colorError: '#F2708A',
          colorBgBase: '#0E1218',
          colorBgContainer: '#151B26',
          colorBgElevated: '#1C2432',
          colorBgLayout: '#0E1218',
          colorBorder: '#283245',
          colorBorderSecondary: '#1F2836',
          colorText: '#E8ECF4',
          colorTextSecondary: '#8D97AB',
          colorTextTertiary: '#6B7691',
          colorTextQuaternary: '#5A6478',
          borderRadius: 8,
          fontFamily: `-apple-system, 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', 'Segoe UI', Roboto, sans-serif`,
        },
        components: {
          Table: { headerBg: '#171E2B', rowHoverBg: '#1C2432' },
          Tabs: { itemSelectedColor: '#7B93FF', inkBarColor: '#7B93FF' },
        },
      }}
    >
      <ProjectProvider>
        <BrowserRouter>
          <div className="app-shell">
            <AppRail />
            <MainArea />
          </div>
        </BrowserRouter>
      </ProjectProvider>
    </ConfigProvider>
  );
}

export default App;
