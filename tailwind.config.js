/** @type {import('tailwindcss').Config} */
module.exports = {
  // Toggle dark mode with the 'dark' class on <html>
  darkMode: 'class',
  // Tell the CLI which files contain Tailwind class names
  content: [
    './src/promptcut/ui/**/*.html',
    './src/promptcut/ui/**/*.js',
  ],
  theme: {
    fontFamily: {
      sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      mono: ['JetBrains Mono', 'ui-monospace', 'Consolas', 'monospace'],
    },
    extend: {
      colors: {
        // ONE accent color — indigo
        accent: {
          DEFAULT: '#6366f1',
          hover:   '#4f46e5',
          light:   '#e0e7ff',
          dark:    '#3730a3',
        },
        // Status — only used for response state
        applied: { DEFAULT: '#16a34a', light: '#f0fdf4', border: '#86efac' },
        clarify: { DEFAULT: '#d97706', light: '#fffbeb', border: '#fcd34d' },
        rejected: { DEFAULT: '#dc2626', light: '#fef2f2', border: '#fca5a5' },
      },
      transitionDuration: {
        DEFAULT: '150ms',
      },
      animation: {
        'shimmer': 'shimmer 1.5s infinite',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0'  },
        },
      },
    },
  },
  plugins: [],
};
