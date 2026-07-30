
class Components:
    '''
        class `Component` refers to Buhin(部品) in the original implementation.
    '''
    def __init__(self, ignore_version = False) -> None:
        self.hash = dict()
        self.ignore_version = ignore_version

    def search(self, name: str) -> str:
        if name in self.hash:
            return self.hash[name]
        if self.ignore_version and '@' in name:
            base = name[0 : name.find('@')]
            if base in self.hash:
                return self.hash[base]
        return ""

    def push(self, name: str, data: str):
        self.hash[name] = data
    
    set = push
