/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        groww: {
          green: '#00B386',
          'green-dark': '#00956E',
          'green-light': '#E8F5F1',
          'green-pale': '#F0FAF7',
          white: '#FFFFFF',
          'off-white': '#F8FAF9',
          'bg-primary': '#F8FAF9',
          'bg-sidebar': '#FFFFFF',
          'text-primary': '#1A1A2E',
          'text-secondary': '#6B7280',
          'text-muted': '#9CA3AF',
          'border': '#E5E7EB',
          'border-light': '#F0F2F4',
        },
      },
      fontFamily: {
        inter: ['Inter', 'sans-serif'],
      },
      boxShadow: {
        'sidebar': '2px 0 20px rgba(0, 179, 134, 0.05)',
        'card': '0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)',
        'input': '0 0 0 3px rgba(0, 179, 134, 0.12)',
      },
      transitionDuration: {
        '250': '250ms',
      },
    },
  },
  plugins: [],
}
