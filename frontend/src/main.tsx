import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import ViewApp from './ViewApp.tsx';
import './index.css';

const isViewer = import.meta.env.VITE_MODE === 'viewer';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isViewer ? <ViewApp /> : <App />}
  </StrictMode>,
);
