import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('full mobile-first grocery flow works', async ({ page }) => {
  const username = `playwright_${Date.now()}_${Math.floor(Math.random() * 10000)}`;
  await page.goto('/login');

  await page.getByRole('button', { name: 'הרשמה' }).click();
  await page.getByLabel('שם משתמש').fill(username);
  await page.getByLabel('סיסמה').fill('secret123');
  await page.getByRole('button', { name: 'ליצור חשבון' }).click();

  await expect(page).toHaveURL('/');

  const searchInput = page.getByPlaceholder('למשל: חלב, ביצים, קוטג׳');
  await searchInput.fill('חל');
  await expect(page.getByText('צריך לפחות 3 תווים לפני שנשלח חיפוש לשרת.')).toBeVisible();

  await searchInput.fill('חלב');
  await expect(page.getByText('חלב תנובה 3% 1 ליטר')).toBeVisible();

  await page.getByText('חלב תנובה 3% 1 ליטר').first().click();
  await expect(page).toHaveURL(/\/products\//);

  await page.getByRole('button', { name: 'הוסף לרשימה' }).click();
  await page.getByRole('button', { name: /הוסף לרשימה/ }).nth(1).click();

  await page.getByRole('link', { name: 'רשימות' }).click();
  await expect(page).toHaveURL('/lists');
  const listHref = await page.getByRole('link', { name: 'הרשימה שלי' }).getAttribute('href');
  expect(listHref).toBeTruthy();
  await page.goto(`http://127.0.0.1:4173${listHref}`);
  await expect(page).toHaveURL(/\/lists\//);

  await page.getByRole('spinbutton', { name: /כמות עבור/ }).fill('2');
  await page.getByRole('button', { name: 'השווה סל' }).evaluate((element: HTMLElement) => element.click());

  await expect(page.getByText('השוואת סל מלאה')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'קרפור' }).first()).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(hasHorizontalOverflow).toBe(false);
});

test('core screens are accessible enough to pass axe baseline', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'קונים חכם. משווים מהר.' })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
