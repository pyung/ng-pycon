/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pyconng/templates/**/*.html',
    './pyconng/apps/**/templates/**/*.html',
    './home/templates/**/*.html',
    './search/templates/**/*.html',
    './pyconng/static/js/*.js',
    './pyconng/static/js/themes/*.js',
    // Not js/vendor: scanning a minified library for class-like strings
    // generates utilities nothing uses and inflates every theme's CSS.
    './pyconng/static/css/src/**/*.css',
  ],
  theme: {
    extend: {
      colors: {
        // PyCon Nigeria brand colors - can be customized yearly
        primary: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
        },
        secondary: {
          50: '#fef2f2',
          100: '#fee2e2',
          200: '#fecaca',
          300: '#fca5a5',
          400: '#f87171',
          500: '#ef4444',
          600: '#dc2626',
          700: '#b91c1c',
          800: '#991b1b',
          900: '#7f1d1d',
        },
        accent: {
          50: '#f7fee7',
          100: '#ecfccb',
          200: '#d9f99d',
          300: '#bef264',
          400: '#a3e635',
          500: '#84cc16',
          600: '#65a30d',
          700: '#4d7c0f',
          800: '#3f6212',
          900: '#365314',
        },
      },
      fontFamily: {
        'sans': ['Inter', 'system-ui', 'sans-serif'],
        'display': ['Poppins', 'system-ui', 'sans-serif'],
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-in-out',
        'slide-up': 'slideUp 0.5s ease-out',
        'bounce-light': 'bounceLight 2s infinite',
        'tech-pulse': 'techPulse 3s ease-in-out infinite',
        'creative-float': 'creativeFloat 4s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { transform: 'translateY(20px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        bounceLight: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-10px)' },
        },
        techPulse: {
          '0%, 100%': { 
            transform: 'scale(1)',
            boxShadow: '0 0 0 0 rgba(37, 99, 235, 0.7)'
          },
          '50%': { 
            transform: 'scale(1.05)',
            boxShadow: '0 0 0 10px rgba(37, 99, 235, 0)'
          },
        },
        creativeFloat: {
          '0%, 100%': { 
            transform: 'translateY(0px) rotate(0deg)',
            borderRadius: '20px'
          },
          '33%': { 
            transform: 'translateY(-10px) rotate(1deg)',
            borderRadius: '25px'
          },
          '66%': { 
            transform: 'translateY(-5px) rotate(-1deg)',
            borderRadius: '15px'
          },
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
} 