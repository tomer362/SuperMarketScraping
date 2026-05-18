import { QueryClientProvider } from '@tanstack/react-query';
import { Navigate, Outlet, RouterProvider, createBrowserRouter, useLocation } from 'react-router-dom';
import { queryClient } from './app/queryClient';
import { AuthProvider, useAuth } from './app/AuthProvider';
import { ThemeProvider } from './app/theme';
import AppShell from './components/AppShell';
import AccountPage from './pages/AccountPage';
import GroupPage from './pages/GroupPage';
import ListDetailPage from './pages/ListDetailPage';
import ListsPage from './pages/ListsPage';
import LoginPage from './pages/LoginPage';
import ProductPage from './pages/ProductPage';
import SearchPage from './pages/SearchPage';

const router = createBrowserRouter([
  {
    path: '/login',
    element: <AuthOnlyRoute />,
    children: [{ index: true, element: <LoginPage /> }],
  },
  {
    path: '/',
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <SearchPage /> },
          { path: 'products/:productId', element: <ProductPage /> },
          { path: 'groups/:groupKey', element: <GroupPage /> },
          { path: 'lists', element: <ListsPage /> },
          { path: 'lists/:listId', element: <ListDetailPage /> },
          { path: 'account', element: <AccountPage /> },
        ],
      },
    ],
  },
]);

function ProtectedRoute() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return <FullPageState text="טוען חשבון..." />;
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

function AuthOnlyRoute() {
  const { status } = useAuth();

  if (status === 'loading') {
    return <FullPageState text="טוען..." />;
  }

  if (status === 'authenticated') {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}

function FullPageState({ text }: { text: string }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4 text-slate-500">
      <div className="rounded-[28px] bg-white px-6 py-5 text-center shadow-sm">
        <h1 className="text-xl font-black text-slate-900">Supermarket Compass</h1>
        <p className="mt-2 text-base font-semibold">{text}</p>
      </div>
    </main>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
