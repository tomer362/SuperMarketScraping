import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getApiErrorMessage } from '../api';
import { useAuth } from '../app/AuthProvider';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, register } = useAuth();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? '/';

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <main className="mx-auto flex min-h-screen max-w-5xl flex-col justify-center gap-8 px-4 py-10 lg:grid lg:grid-cols-[1.2fr_0.8fr] lg:items-center lg:px-8">
        <section className="rounded-[36px] border border-white/10 bg-white/5 p-6 shadow-2xl shadow-sky-950/20 backdrop-blur sm:p-10">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-sky-300">Phone-ready grocery app</p>
          <h1 className="mt-4 text-4xl font-black leading-tight sm:text-5xl">
            קונים חכם.
            <br />
            משווים מהר.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-slate-300 sm:text-lg">
            חפשו מוצר, קבלו הצעות אוטומטיות אחרי 3 תווים, בנו כמה רשימות שתרצו, והשוו כל סל לפי הרשת הזולה ביותר בפועל.
          </p>
          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            <FeaturePill title="Autocomplete" text="למובייל, מהיר ונקי" />
            <FeaturePill title="Basket Logic" text="כולל כמויות ומבצעים" />
            <FeaturePill title="Phone First" text="עובד מצוין גם בדסקטופ" />
          </div>
        </section>

        <section className="rounded-[36px] bg-white p-6 text-slate-900 shadow-[0_30px_100px_-40px_rgba(56,189,248,0.55)] sm:p-8" aria-label="טופס התחברות והרשמה">
          <div className="flex gap-2 rounded-full bg-slate-100 p-1">
            <button
              type="button"
              className={`flex-1 rounded-full px-4 py-2 text-sm font-bold transition ${
                mode === 'login' ? 'bg-slate-900 text-white' : 'text-slate-600'
              }`}
              onClick={() => setMode('login')}
            >
              התחברות
            </button>
            <button
              type="button"
              className={`flex-1 rounded-full px-4 py-2 text-sm font-bold transition ${
                mode === 'register' ? 'bg-slate-900 text-white' : 'text-slate-600'
              }`}
              onClick={() => setMode('register')}
            >
              הרשמה
            </button>
          </div>

          <form
            className="mt-6 space-y-4"
            onSubmit={async (event) => {
              event.preventDefault();
              setSubmitting(true);
              setError(null);
              try {
                if (mode === 'login') {
                  await login(username, password);
                } else {
                  await register(username, password);
                }
                navigate(redirectTo, { replace: true });
              } catch (err) {
                setError(getApiErrorMessage(err, 'לא הצלחנו להשלים את הפעולה.'));
              } finally {
                setSubmitting(false);
              }
            }}
          >
            <div>
              <label htmlFor="username" className="mb-2 block text-sm font-semibold text-slate-700">
                שם משתמש
              </label>
              <input
                id="username"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="min-h-13 w-full rounded-[22px] border border-slate-200 bg-slate-50 px-4 text-base outline-none transition focus:border-sky-300 focus:bg-white"
                placeholder="שם משתמש"
                dir="ltr"
                required
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-2 block text-sm font-semibold text-slate-700">
                סיסמה
              </label>
              <input
                id="password"
                type="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="min-h-13 w-full rounded-[22px] border border-slate-200 bg-slate-50 px-4 text-base outline-none transition focus:border-sky-300 focus:bg-white"
                placeholder="6 תווים ומעלה"
                dir="ltr"
                required
              />
            </div>

            {error && <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">{error}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="min-h-13 w-full rounded-[22px] bg-slate-900 px-4 text-base font-black text-white transition hover:bg-slate-800 disabled:opacity-60"
            >
              {submitting ? 'טוען...' : mode === 'login' ? 'להתחבר' : 'ליצור חשבון'}
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}

function FeaturePill({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-[24px] border border-white/10 bg-white/5 px-4 py-4">
      <p className="text-sm font-bold text-white">{title}</p>
      <p className="mt-1 text-sm text-slate-300">{text}</p>
    </div>
  );
}
