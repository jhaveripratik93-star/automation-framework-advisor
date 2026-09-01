# Chef — Infrastructure automation example (Recipe)
# Install: gem install chef
# Run:     chef-client --local-mode chef_example.rb

# Example: install test dependencies on a node
package %w[python3 python3-pip git] do
  action :install
end

execute "install_test_deps" do
  command "pip3 install pytest playwright"
  not_if "pip3 show playwright"
end

directory "/opt/tests" do
  owner "root"
  group "root"
  mode  "0755"
  action :create
end

execute "run_smoke_tests" do
  command "pytest /opt/tests/smoke/ -v --tb=short"
  cwd     "/opt/tests"
  returns [0]
end
