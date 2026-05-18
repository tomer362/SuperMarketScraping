import { useEffect, useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  dismissLocationPrompt,
  getCatalogStatus,
  getChains,
  saveCurrentLocation,
  triggerCatalogRefresh,
} from '../api';
import { resolvePreferredChains, subscribePreferredChainsChange } from '../app/chainPreferences';
import { useAuth } from '../app/AuthProvider';
import { useTheme } from '../app/theme';
import { formatRelativeDate } from '../lib/format';
import { classNames } from '../lib/classNames';
import { parseSearchQuantity } from '../lib/queryQuantity';
import SearchAutocomplete from './SearchAutocomplete';
import type { CatalogStatus } from '../types';

export default function AppShell() {
  const { user, logout, refresh } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [progressOpen, setProgressOpen] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [autocompleteHiddenForQuery, setAutocompleteHiddenForQuery] = useState<string | null>(null);
  const [selectedChains, setSelectedChains] = useState<string[]>([]);

  const statusQuery = useQuery({
    queryKey: ['catalog-status'],
    queryFn: getCatalogStatus,
    refetchInterval: (query) =>
      query.state.data?.refresh_in_progress || progressOpen ? 1_500 : 45_000,
  });
  const refreshInProgress = Boolean(statusQuery.data?.refresh_in_progress);

  const chainsQuery = useQuery({ queryKey: ['chains'], queryFn: getChains });
  const activeChains = useMemo(
    () => (chainsQuery.data ?? []).filter((chain) => chain.enabled),
    [chainsQuery.data],
  );

  useEffect(() => {
    if (activeChains.length === 0) {
      setSelectedChains([]);
      return;
    }
    const syncSelection = () => {
      setSelectedChains(resolvePreferredChains(activeChains.map((chain) => chain.chain)));
    };
    syncSelection();
    return subscribePreferredChainsChange(syncSelection);
  }, [activeChains]);

  useEffect(() => {
    if (location.pathname !== '/') {
      return;
    }
    setSearchText(new URLSearchParams(location.search).get('q') ?? '');
  }, [location.pathname, location.search]);

  const refreshMutation = useMutation({
    mutationFn: triggerCatalogRefresh,
    onMutate: () => setProgressOpen(true),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['catalog-status'] });
    },
  });

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

  const parsedSearch = parseSearchQuantity(searchText);
  const searchAutocompleteQuery = parsedSearch.cleanedQuery.trim();
  const showAutocomplete =
    searchAutocompleteQuery.length >= 3 && autocompleteHiddenForQuery !== searchText.trim();
  const quantityParams = parsedSearch.quantity
    ? `?qty=${encodeURIComponent(String(parsedSearch.quantity.value))}&dim=${encodeURIComponent(parsedSearch.quantity.dimension)}`
    : '';

  const submitGlobalSearch = () => {
    const nextQuery = searchText.trim();
    setAutocompleteHiddenForQuery(nextQuery);
    if (!nextQuery) {
      navigate('/');
      return;
    }
    navigate(`/?q=${encodeURIComponent(nextQuery)}`);
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_30%,_#f8fafc_60%)] text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-3 pb-8 pt-3 sm:px-6 lg:px-8">
        <header className="sticky top-2 z-30 rounded-[22px] border border-white/80 bg-white/90 px-3 py-3 shadow-[0_18px_50px_-28px_rgba(15,23,42,0.32)] backdrop-blur sm:rounded-[24px] sm:px-5">
          <div className="grid gap-2 lg:grid-cols-[auto_minmax(20rem,1fr)_auto] lg:items-center">
            <div className="min-w-0">
              <p className="text-[0.64rem] font-semibold uppercase tracking-[0.18em] text-sky-600 sm:text-[0.68rem] sm:tracking-[0.22em]">
                Supermarket Compass
              </p>
              <h1 className="mt-0.5 text-lg font-black leading-tight text-slate-900 sm:mt-1 sm:text-2xl">
                השוואת סל חכמה
              </h1>
            </div>

            <form
              className="relative"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                submitGlobalSearch();
              }}
            >
              <div className="flex min-h-12 overflow-hidden rounded-[20px] border border-slate-200 bg-slate-50 shadow-inner focus-within:border-sky-300 focus-within:bg-white">
                <input
                  type="search"
                  value={searchText}
                  onChange={(event) => {
                    setSearchText(event.target.value);
                    setAutocompleteHiddenForQuery(null);
                  }}
                  onFocus={() => {
                    if (searchText.trim() !== autocompleteHiddenForQuery) {
                      setAutocompleteHiddenForQuery(null);
                    }
                  }}
                  placeholder="חיפוש מוצר להשוואת מחירים"
                  className="min-w-0 flex-1 border-0 bg-transparent px-4 text-base outline-none"
                />
                <button
                  type="submit"
                  className="shrink-0 bg-slate-900 px-5 text-sm font-black text-white transition hover:bg-slate-800"
                >
                  חיפוש
                </button>
              </div>
              {showAutocomplete && (
                <div className="absolute inset-x-0 top-full z-40 mt-2">
                  <SearchAutocomplete
                    query={searchAutocompleteQuery}
                    chains={selectedChains}
                    onSelect={(productId) => {
                      setAutocompleteHiddenForQuery(searchText.trim());
                      navigate(`/products/${productId}${quantityParams}`);
                    }}
                  />
                </div>
              )}
            </form>

            <div className="flex flex-wrap items-center gap-1.5 lg:justify-end">
              <TopNavItem to="/lists" label="רשימות" />
              <TopNavItem to="/account" label="הגדרות" />
              <button
                type="button"
                onClick={() => {
                  if (refreshInProgress) {
                    setProgressOpen(true);
                  } else {
                    refreshMutation.mutate();
                  }
                }}
                className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-2 text-xs font-bold text-sky-700 transition hover:bg-sky-100 disabled:cursor-not-allowed disabled:opacity-60 sm:px-3"
                disabled={refreshMutation.isPending}
                aria-label={refreshInProgress ? 'הצג התקדמות' : 'רענן קטלוג'}
              >
                {refreshInProgress ? 'התקדמות' : refreshMutation.isPending ? 'מתחיל...' : 'רענן'}
              </button>
              <button
                type="button"
                onClick={toggleTheme}
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 transition hover:bg-slate-50"
                aria-label="החלף מצב תצוגה"
              >
                {theme === 'dark' ? 'בהיר' : 'כהה'}
              </button>
              <button
                type="button"
                onClick={async () => {
                  await logout();
                  navigate('/login');
                }}
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 transition hover:bg-slate-50"
              >
                יציאה
              </button>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 font-semibold text-slate-600">
              <span
                className={classNames(
                  'inline-block h-2.5 w-2.5 rounded-full',
                  statusQuery.data?.catalog_fresh ? 'bg-emerald-400' : 'bg-amber-400',
                )}
              />
              {statusQuery.data?.catalog_fresh ? 'קטלוג עדכני' : 'הקטלוג ישן'}
            </span>
            <span className="hidden rounded-full bg-slate-100 px-3 py-1.5 sm:inline-flex">
              עודכן: {formatRelativeDate(statusQuery.data?.last_successful_refresh?.finished_at)}
            </span>
            <span className="hidden rounded-full bg-slate-100 px-3 py-1.5 sm:inline-flex">מחובר/ת: {user?.username}</span>
          </div>
        </header>

        {showLocationPrompt && (
          <section className="mt-4 rounded-[22px] border border-sky-100 bg-white/95 px-4 py-4 shadow-sm sm:px-5">
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

        <main className="flex-1 py-5">
          <Outlet />
        </main>
      </div>

      {progressOpen && (
        <RefreshProgressDialog
          status={statusQuery.data}
          isStarting={refreshMutation.isPending}
          error={refreshMutation.error}
          onClose={() => setProgressOpen(false)}
        />
      )}
    </div>
  );
}

function TopNavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        classNames(
          'rounded-full border px-3 py-2 text-xs font-bold transition',
          isActive
            ? 'border-sky-200 bg-sky-100 text-sky-950'
            : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
        )
      }
    >
      {label}
    </NavLink>
  );
}

function RefreshProgressDialog({
  status,
  isStarting,
  error,
  onClose,
}: {
  status?: CatalogStatus;
  isStarting: boolean;
  error: unknown;
  onClose: () => void;
}) {
  const progress = status?.active_refresh;
  const percent = isStarting ? 0 : progress?.progress_percent ?? (status?.refresh_in_progress ? 0 : 100);
  const running = isStarting || status?.refresh_in_progress;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 px-4 py-8 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="refresh-progress-title"
        className="w-full max-w-lg rounded-[28px] border border-white/80 bg-white p-5 text-slate-900 shadow-2xl sm:p-6"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-sky-600">Catalog refresh</p>
            <h2 id="refresh-progress-title" className="mt-2 text-2xl font-black">
              התקדמות סריקה
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-bold text-slate-600 transition hover:bg-slate-50"
          >
            סגור
          </button>
        </div>

        <div className="mt-5">
          <div className="mb-2 flex items-center justify-between gap-3 text-sm font-bold text-slate-700">
            <span>{progress?.current_status_label ?? (isStarting ? 'מתחיל רענון קטלוג...' : 'אין רענון פעיל')}</span>
            <span>{percent}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-sky-500 transition-all duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <ProgressMetric
            label="רשתות"
            value={`${progress?.completed_chains ?? 0}/${progress?.total_chains ?? status?.chains.length ?? 0}`}
          />
          <ProgressMetric label="מוצרים" value={String(progress?.products_upserted ?? 0)} />
          <ProgressMetric label="מצב" value={running ? 'רץ' : progress?.status === 'failed' ? 'נכשל' : 'הושלם'} />
        </div>

        {progress && (progress.chains_scraped.length > 0 || progress.chains_failed.length > 0) && (
          <div className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            <ProgressList title="הושלמו" items={progress.chains_scraped} tone="success" />
            <ProgressList title="נכשלו" items={progress.chains_failed} tone="danger" />
          </div>
        )}

        {Boolean(error) && (
          <p className="mt-4 rounded-2xl bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700">
            לא הצלחנו להתחיל רענון קטלוג.
          </p>
        )}
      </section>
    </div>
  );
}

function ProgressMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
      <p className="text-xs font-semibold text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-black text-slate-900">{value}</p>
    </div>
  );
}

function ProgressList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: 'success' | 'danger';
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
      <p className={classNames('text-sm font-black', tone === 'success' ? 'text-emerald-700' : 'text-rose-700')}>
        {title}
      </p>
      <p className="mt-2 text-slate-600">{items.length > 0 ? items.join(', ') : 'אין'}</p>
    </div>
  );
}
