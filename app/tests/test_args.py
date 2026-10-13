import optparse

def main():
    # 1. Initialize the parser
    parser = optparse.OptionParser(usage="usage: %prog [options] arguments")
    
    # 2. Add options
    # A standard string input option
    parser.add_option("-f", "--file", 
                      dest="filename", 
                      help="Write output to FILE", 
                      metavar="FILE")
                      
    # A boolean flag (stores True if provided, defaults to False)
    parser.add_option("-v", "--verbose",
                      action="store_true", 
                      dest="verbose", 
                      default=False,
                      help="Print verbose status messages")

    # 3. Parse the command-line arguments
    (options, args) = parser.parse_args()
    print('***********************************')
    print(options, args)
    print('***********************************')
    # 4. Use the parsed values
    if options.verbose:
        print("Verbose mode is enabled.")
        
    if options.filename:
        print(f"Output will be saved to: {options.filename}")
    else:
        print("No output file specified.")
        
    # 'args' contains any remaining positional arguments
    if args:
        print(f"Remaining positional arguments: {args}")

if __name__ == "__main__":
    main()
