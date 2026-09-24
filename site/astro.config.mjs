import { defineConfig } from 'astro/config';

// GitHub Pages serves a project site from /<repo>/, so both `site` and `base`
// come from the workflow environment. Locally they are empty and the site is
// served from the root.
const site = process.env.SITE_URL || undefined;
const base = process.env.BASE_PATH || undefined;

export default defineConfig({
  site,
  base,
  output: 'static',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  devToolbar: {
    enabled: false,
  },
});
