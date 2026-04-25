import os
import sys

# Add current directory to path so we can import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main():
    try:
        from utils import process_js_files, compile_scss
        print("Processing JS files...")
        process_js_files()
        print("Compiling SCSS files...")
        compile_scss()
        print("Build assets completed successfully.")
    except Exception as e:
        print(f"Error building assets: {e}")
        # We don't exit with 1 because we want the app to try to start even if build fails

if __name__ == "__main__":
    main()
