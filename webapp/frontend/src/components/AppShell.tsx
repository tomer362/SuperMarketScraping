import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { dismissLocationPrompt, getCatalogStatus, saveCurrentLocation, triggerCatalogRefresh } from '../api';
import { useAuth } from '../app/AuthProvider';
import { useTheme } from '../app/theme';
import { formatRelativeDate } from '../lib/format';
import { classNames } from '../lib/classNames';

export default function AppShell() {
  const { user, logout, refresh } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: ['catalog-status'],
    queryFn: getCatalogStatus,
    refetchInterval: (query) => (query.state.data?.refresh_in_progress ? 10_000 : 45_000),
  });

  const refreshMutation = useMutation({
    mutationFn: triggerCatalogRefresh,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['catalog-status'] }),
      ]);
    },
  });
  const refreshInProgress = Boolean(statusQuery.data?.refresh_in_progress);
  const showLocationPrompt = Boolean(user && !user.location_lat && !user.location_prompt_dismissed);
  const locationPromptMutation = useMutation({
    mutationFn: dismissLocationPrompt,
    onSuccess: async () => refresh(),
  });
  const gpsMutation = useMutation({
    mutationFn: () =>
      new Promise<void>((resolve, reject) => {
        if (!navigator.geolocation) {
          reject(new Error('geolocation unavailable'));
          return;
        }
        navigator.geolocation.getCurrentPosition(
          (position) => {
            saveCurrentLocation(
              position.coords.latitude,
              position.coords.longitude,
              'המיקום הנוכחי',
            ).then(() => resolve()).catch(reject);
          },
          reject,
          { enableHighAccuracy: true, timeout: 10000 },
        );
      }),
    onSuccess: async () => refresh(),
    onError: async () => locationPromptMutation.mutate(true),
  });

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_30%,_#f8fafc_60%)] text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-3 pb-28 pt-4 sm:px-6 lg:px-8">
        <header className="sticky top-3 z-30 rounded-[28px] border border-white/80 bg-white/85 px-4 py-4 shadow-[0_18px_50px_-24px_rgba(15,23,42,0.3)] backdrop-blur sm:px-6">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-600">
                Supermarket Compass
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-black text-slate-900 sm:text-3xl">
                  השוואת סל חכמה
                </h1>
                <span className="rounded-full bg-slate-900 px-3 py-1 text-xs font-semibold text-white">
                  Mobile first
                </span>
              </div>
              <p className="max-w-2xl text-sm leading-6 text-slate-500 sm:text-base">
                חיפוש מהיר, השוואת מחירים לפי רשת, רשימות קניות עם כמויות, והעדפות שמתאימות קודם כל לטלפון.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="rounded-3xl border border-slate-100 bg-slate-50/80 px-4 py-3 text-sm text-slate-600 shadow-inner">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="inline-flex items-center gap-2 font-medium text-slate-700">
                    <span
                      className={classNames(
                        'inline-block h-2.5 w-2.5 rounded-full',
                        statusQuery.data?.catalog_fresh ? 'bg-emerald-400' : 'bg-amber-400',
                      )}
                    />
                    {statusQuery.data?.catalog_fresh ? 'קטלוג עדכני' : 'הקטלוג ישן יותר מהרגיל'}
                  </span>
                  <span>עדכון אחרון: {formatRelativeDate(statusQuery.data?.last_successful_refresh?.finished_at)}</span>
                </div>
              </div>

              <div className="flex items-center justify-between gap-2 sm:justify-end">
                <button
                  type="button"
                  onClick={() => refreshMutation.mutate()}
                  className="rounded-full border border-sky-200 bg-sky-50 px-4 py-2 text-sm font-semibold text-sky-700 transition hover:bg-sky-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={refreshMutation.isPending || refreshInProgress}
                >
                  {refreshMutation.isPending || refreshInProgress ? 'מעדכן קטלוג...' : 'רענן קטלוג'}
                </button>
                <button
                  type="button"
                  onClick={toggleTheme}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                  aria-label="החלף מצב תצוגה"
                >
                  {theme === 'dark' ? 'מצב בהיר' : 'מצב כהה'}
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    await logout();
                    navigate('/login');
                  }}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  יציאה
                </button>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span className="rounded-full bg-slate-100 px-3 py-1.5 font-medium text-slate-600">
              מחובר/ת: {user?.username}
            </span>
          </div>
        </header>

        {showLocationPrompt && (
          <section className="mt-4 rounded-[28px] border border-sky-100 bg-white/95 px-4 py-4 shadow-sm sm:px-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-black text-slate-900">להציג סופרים קרובים יותר?</p>
                <p className="mt-1 text-sm text-slate-500">
                  שמירת מיקום תוסיף מרחק להשוואה ותעדיף סניפים קרובים כשהמחיר זהה.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => gpsMutation.mutate()}
                  disabled={gpsMutation.isPending}
                  className="rounded-full bg-slate-900 px-4 py-2 text-sm font-bold text-white transition hover:bg-slate-800 disabled:opacity-60"
                >
                  השתמש במיקום
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/account')}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
                >
                  הגדרות
                </button>
                <button
                  type="button"
                  onClick={() => locationPromptMutation.mutate(true)}
                  className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-500 transition hover:bg-slate-50"
                >
                  לא עכשיו
                </button>
              </div>
            </div>
          </section>
        )}

        <main className="flex-1 py-6">
          <Outlet />
        </main>

        <nav className="fixed inset-x-0 bottom-0 z-40 mx-auto w-full max-w-5xl px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] sm:px-6">
          <div className="grid grid-cols-3 gap-2 rounded-[30px] border border-slate-200/80 bg-white/95 p-2 shadow-[0_18px_60px_-24px_rgba(15,23,42,0.35)] backdrop-blur">
            <BottomNavItem to="/" label="חיפוש" />
            <BottomNavItem to="/lists" label="רשימות" />
            <BottomNavItem to="/account" label="הגדרות" />
          </div>
        </nav>
      </div>
    </div>
  );
}

function BottomNavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        classNames(
          'flex min-h-14 items-center justify-center rounded-[22px] px-4 text-sm font-semibold transition',
          isActive
            ? 'border border-sky-200 bg-sky-100 text-sky-950 shadow-sm'
            : 'bg-slate-50 text-slate-600 hover:bg-slate-100',
        )
      }
    >
      {label}
    </NavLink>
  );
}
